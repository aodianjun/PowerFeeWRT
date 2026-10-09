'use strict';
'require view';
'require fs';
'require ui';
'require dom';
'require poll';
'require rpc';

/*
 * 宿舍电量哨兵 · 状态页
 *
 * 所有数据都来自主程序 /usr/bin/powerfee 的机器可读接口（json status / json check /
 * json test-mail / json selftest / log），通过 ubus 的 file exec 调用；
 * 对应授权写在 /usr/share/rpcd/acl.d/luci-app-powerfee.json。
 */

var PF_BIN = '/usr/bin/powerfee';
var PF_INIT = '/etc/init.d/powerfee';
var POLL_SECONDS = 15;

var callServiceList = rpc.declare({
	object: 'service',
	method: 'list',
	params: [ 'name' ],
	expect: { '': {} }
});

var LEVEL_COLOR = {
	ok: '#188038',
	warn: '#e8710a',
	low: '#d93025',
	unknown: '#5f6368'
};

var LEVEL_TEXT = {
	ok: _('电量充足'),
	warn: _('电量预警'),
	low: _('电费不足'),
	unknown: _('未知')
};

var REASON_TEXT = {
	'ok->warn': _('进入预警区'),
	'warn->low': _('电量降到不足'),
	'unknown->low': _('首次查询即为低电量'),
	'repeat-low': _('持续低电量重复提醒'),
	'recovered': _('充值后已恢复'),
	'stale': _('监控失效（长时间取不到数据）'),
	'stale-ok': _('监控已恢复'),
	'force': _('手动强制发送'),
	'test': _('测试邮件')
};

var ACTION_TEXT = {
	start: _('启动'),
	stop: _('停止'),
	restart: _('重启')
};

var STYLE = '' +
	'.powerfee-log{max-height:340px;overflow:auto;background:#f8f9fa;border:1px solid #dadce0;' +
	'border-radius:4px;padding:10px;font-family:monospace;font-size:12px;white-space:pre-wrap;' +
	'word-break:break-all;margin:0}' +
	'.powerfee-overview .cbi-value-title{width:32%}' +
	'.powerfee-badge{display:inline-block;padding:2px 10px;border-radius:10px;color:#fff;font-size:12px}';

/* ---------- 小工具 ---------- */

/* ui.createHandlerFn 的参数顺序在新旧 LuCI 里是**反的**：
   老版（≤23.05）传 (事件, …附加参数)，新版（26.x）传 (…附加参数, 事件)。
   用「有没有 currentTarget」认出事件并丢掉，只留附加参数 —— 两代都不会崩。 */
function handlerArgs(args) {
	var out = [];
	Array.prototype.forEach.call(args, function(x) {
		if (!(x && typeof x === 'object' && 'currentTarget' in x))
			out.push(x);
	});
	return out;
}

function dash() {
	return '—';
}

/* 电量单位：优先用主程序返回的 unit，取不到就回退「度」 */
function unitOf(s) {
	return (s && s.unit) ? String(s.unit) : _('度');
}

function num(v, digits) {
	if (v === null || v === undefined || v === '' || isNaN(v))
		return dash();
	return Number(v).toFixed(digits == null ? 2 : digits);
}

function qty(v, digits, unit) {
	if (v === null || v === undefined || v === '' || isNaN(v))
		return dash();
	return '%s %s'.format(num(v, digits), unit);
}

function pad2(n) {
	return (n < 10 ? '0' : '') + n;
}

function fmtTime(ts) {
	if (ts === null || ts === undefined || ts === '' || isNaN(ts) || Number(ts) <= 0)
		return dash();
	var d = new Date(Number(ts) * 1000);
	return '%d-%s-%s %s:%s:%s'.format(d.getFullYear(), pad2(d.getMonth() + 1), pad2(d.getDate()),
		pad2(d.getHours()), pad2(d.getMinutes()), pad2(d.getSeconds()));
}

function badge(text, color) {
	return E('span', { 'class': 'powerfee-badge', 'style': 'background:%s'.format(color || LEVEL_COLOR.unknown) }, text);
}

function row(label, value) {
	return E('div', { 'class': 'cbi-value' }, [
		E('label', { 'class': 'cbi-value-title' }, label),
		E('div', { 'class': 'cbi-value-field' }, value)
	]);
}

function levelBadge(level) {
	level = level || 'unknown';
	return badge(LEVEL_TEXT[level] || level, LEVEL_COLOR[level] || LEVEL_COLOR.unknown);
}

function reasonText(reason) {
	if (!reason)
		return dash();
	var text = REASON_TEXT[reason];
	return text ? '%s（%s）'.format(text, reason) : reason;
}

/* 一个房间的送达方式摘要（邮件收件人 + 推送地址，地址已由主程序掩码） */
function deliveryText(r) {
	var parts = [];
	if (r.mail_to)
		parts.push(_('邮件 → %s').format(r.mail_to));
	else
		parts.push(_('邮件 → 未配置'));
	if (r.notify_enabled === false)
		parts.push(_('推送 → 未启用'));
	else if (r.notify_url)
		parts.push(_('推送 → %s').format(r.notify_url));
	else
		parts.push(_('推送 → 未配置'));
	return parts.join(' ｜ ');
}

/* 房间用的发件 / 推送账号与推送通道（1.2.0 起；字段不存在时不显示，兼容旧引擎） */
function accountText(r) {
	var parts = [];
	if (r.mail_account !== undefined) {
		var m = r.mail_account || _('默认账号');
		if (r.mail_account && r.mail_account_effective !== undefined && !r.mail_account_effective)
			m = _('%s（不可用）').format(r.mail_account);
		parts.push(_('发件：%s').format(m));
	}
	if (r.notify_account !== undefined) {
		var p = r.notify_account || _('默认账号');
		if (r.notify_account && r.notify_account_effective !== undefined && !r.notify_account_effective)
			p = _('%s（不可用）').format(r.notify_account);
		parts.push(_('推送：%s').format(p));
	}
	var ch = (r.notify_channel_name !== undefined && r.notify_channel_name !== '')
		? r.notify_channel_name : r.notify_channel;
	if (ch !== undefined && ch !== null && ch !== '')
		parts.push(_('通道：%s').format(ch));
	return parts.join(' ｜ ');
}

/* 调用 powerfee，返回 stdout 文本；非 0 退出码抛错 */
function pfExec(args) {
	return fs.exec(PF_BIN, args).then(function(res) {
		if (res.code !== 0) {
			var msg = ((res.stderr || '') + (res.stdout || '')).trim();
			throw new Error(msg || _('powerfee 执行失败（退出码 %s）').format(res.code));
		}
		return res.stdout || '';
	});
}

/* 调用 powerfee 的 json 子命令，解析成对象 */
function pfJson(args) {
	return pfExec(args).then(function(out) {
		try {
			return JSON.parse(out);
		}
		catch (e) {
			throw new Error(_('无法解析 powerfee 的输出：%s').format(String(out).trim().slice(0, 200)));
		}
	});
}

/* 弹一个「请稍候」模态框，等 promise 结束（成功或失败）后自动关掉 */
function withBusy(title, promise) {
	ui.showModal(title, [ E('p', { 'class': 'spinning' }, _('请稍候…')) ]);
	return promise.then(function(res) {
		ui.hideModal();
		return res;
	}, function(err) {
		ui.hideModal();
		throw err;
	});
}

function notifyError(err) {
	ui.addNotification(null, E('p', {}, _('操作失败：%s').format(err && err.message ? err.message : String(err))), 'danger');
}

/* ---------- 页面 ---------- */

return view.extend({
	/* 状态页没有 UCI 表单，隐藏 LuCI 自动生成的保存/应用按钮 */
	handleSave: null,
	handleSaveApply: null,
	handleReset: null,

	load: function() {
		return Promise.all([
			pfJson([ 'json', 'status' ]).then(function(s) {
				return { status: s };
			}, function(e) {
				return { error: e };
			}),
			L.resolveDefault(callServiceList('powerfee'), {}),
			L.resolveDefault(pfExec([ 'log', '40' ]), '')
		]);
	},

	render: function(data) {
		var self = this;

		this.status = (data[0] && data[0].status) || null;
		this.statusError = (data[0] && data[0].error) || null;
		this.svc = data[1] || {};
		this.logText = data[2] || '';

		this.statusNode = E('div');
		this.usageNode = E('div');
		this.mailNode = E('div');
		this.serviceNode = E('div');
		this.logPre = E('pre', { 'class': 'powerfee-log' }, this.logText || _('（日志为空）'));

		var view = E('div', { 'class': 'powerfee-overview' }, [
			E('style', {}, STYLE),
			E('h2', {}, _('宿舍电量哨兵')),

			E('div', { 'class': 'cbi-section' }, this.statusNode),
			E('div', { 'class': 'cbi-section' }, this.usageNode),
			E('div', { 'class': 'cbi-section' }, this.mailNode),
			E('div', { 'class': 'cbi-section' }, this.serviceNode),

			E('div', { 'class': 'cbi-section' }, [
				E('h3', {}, _('操作')),
				E('div', {}, [
					E('button', {
						'class': 'cbi-button cbi-button-action important',
						'click': ui.createHandlerFn(this, 'handleCheck')
					}, _('立即查询')),
					' ',
					E('button', {
						'class': 'cbi-button cbi-button-action',
						'click': ui.createHandlerFn(this, 'handleTestMail')
					}, _('发送测试邮件')),
					' ',
					E('button', {
						'class': 'cbi-button',
						'click': ui.createHandlerFn(this, 'handleSelftest')
					}, _('运行自检'))
				])
			]),

			E('div', { 'class': 'cbi-section' }, [
				E('h3', {}, _('运行日志')),
				E('p', { 'class': 'cbi-value-description' }, _('最近 40 行（等同 powerfee log 40）。服务在后台自动查询，这里能看到每一次查询的结果。')),
				this.logPre,
				E('p', {}, E('button', {
					'class': 'cbi-button cbi-button-reload',
					'click': ui.createHandlerFn(this, 'handleRefreshLog')
				}, _('刷新日志')))
			])
		]);

		this.fillStatus();
		this.fillMail();
		this.fillService();
		this.fillUsage();       /* 最近 7 天用电量：单独异步取，取不到就整块不显示 */

		poll.add(function() {
			return self.refresh();
		}, POLL_SECONDS);

		return view;
	},

	/* ---------- 数据刷新 ---------- */

	refresh: function() {
		var self = this;
		return Promise.all([
			pfJson([ 'json', 'status' ]).then(function(s) {
				return s;
			}, function() {
				return null;
			}),
			L.resolveDefault(callServiceList('powerfee'), {})
		]).then(function(res) {
			if (res[0]) {
				self.status = res[0];
				self.statusError = null;
			}
			self.svc = res[1] || {};
			self.fillStatus();
			self.fillMail();
			self.fillService();
		});
	},

	/* ---------- 各卡片渲染 ---------- */

	fillStatus: function() {
		var s = this.status;
		var nodes = [];

		if (this.statusError) {
			nodes.push(E('div', { 'class': 'alert-message danger' }, [
				E('h4', {}, _('无法读取 powerfee 状态')),
				E('p', {}, this.statusError.message || String(this.statusError)),
				E('p', {}, _('请确认 /usr/bin/powerfee 已安装，且 ACL（/usr/share/rpcd/acl.d/luci-app-powerfee.json）已生效。'))
			]));
		}

		if (!s) {
			dom.content(this.statusNode, nodes.length ? nodes : E('p', {}, E('em', {}, _('读取中…'))));
			return;
		}

		if (!s.configured) {
			nodes.push(E('div', { 'class': 'alert-message warning' }, [
				E('h4', {}, _('还没有选择要监控的宿舍')),
				E('p', {}, _('请先到「宿舍管理」页选定房间。选好后这里会显示余额、档位、日均用量、提醒记录和服务状态。')),
				E('p', {}, E('a', {
					'class': 'btn cbi-button cbi-button-action',
					'href': L.url('admin/services/powerfee/room')
				}, _('去管理宿舍')))
			]));
		}

		if (s.enabled === false) {
			nodes.push(E('div', { 'class': 'alert-message warning' }, [
				E('p', {}, [
					_('总开关已关闭（main.enabled=0）：服务不会自动查询，也不会发邮件。'),
					' ',
					E('a', { 'href': L.url('admin/services/powerfee/settings') }, _('去设置'))
				])
			]));
		}

		if (s.api_configured === false) {
			nodes.push(E('div', { 'class': 'alert-message warning' }, [
				E('p', {}, [
					_('还没有配置电费查询接口（api.url），无法查询余额。'),
					' ',
					E('a', { 'href': L.url('admin/services/powerfee/settings') }, _('去设置里填接口地址'))
				])
			]));
		}

		if (s.last_error) {
			nodes.push(E('div', { 'class': 'alert-message warning' }, [
				E('p', {}, _('最近一次查询错误：%s').format(s.last_error))
			]));
		}

		var level = s.level || 'unknown';
		var color = LEVEL_COLOR[level] || LEVEL_COLOR.unknown;
		var unit = unitOf(s);

		if (s.rooms && s.rooms.length > 1) {
			/* 多房间：逐个房间列余额（顶层字段是第一个房间的，只作为兜底显示） */
			nodes.push(E('h3', {}, _('监控房间（%s 个）').format(s.rooms.length)));
			var roomRows = [ E('tr', { 'class': 'tr table-titles' }, [
				E('th', {}, _('房间')),
				E('th', {}, _('编号')),
				E('th', {}, _('余额')),
				E('th', {}, _('档位')),
				E('th', {}, _('阈值')),
				E('th', {}, _('上次查询')),
				E('th', {}, _('送达')),
				E('th', {}, _('账号 / 通道'))
			]) ];
			s.rooms.forEach(function(r) {
				var rl = r.level || 'unknown';
				roomRows.push(E('tr', { 'class': 'tr', 'style': r.enabled === false ? 'opacity:.55' : null }, [
					E('td', {}, [
						E('strong', {}, r.display || dash()),
						r.enabled === false ? ' ' + E('em', {}, _('（已停用）')) : ''
					]),
					E('td', {}, r.num || dash()),
					E('td', {}, (r.balance === null || r.balance === undefined)
						? dash() : '%s %s'.format(num(r.balance), unit)),
					E('td', {}, levelBadge(rl)),
					E('td', {}, '%s %s'.format(num(r.threshold, 0), unit)),
					E('td', { 'style': 'font-size:12px' }, fmtTime(r.last_ok_at)),
					E('td', { 'style': 'font-size:12px' }, deliveryText(r)),
					E('td', { 'style': 'font-size:12px' }, accountText(r) || dash())
				]));
			});
			nodes.push(E('table', { 'class': 'table' }, roomRows));
			nodes.push(E('p', {}, E('a', {
				'class': 'btn cbi-button',
				'href': L.url('admin/services/powerfee/room')
			}, _('管理房间（增删改、单独设置收件人 / 发件账号 / 推送账号）'))));
		}
		else {
			nodes.push(E('h3', {}, _('当前状态')));
			nodes.push(row(_('房间'), s.configured
				? E('span', {}, [
					E('strong', {}, s.room_display || dash()),
					' ',
					E('span', { 'class': 'cbi-value-description' }, '（%s%s）'.format(s.campus || dash(),
						s.room_num ? _('，房间编号 %s').format(s.room_num) : ''))
				])
				: E('em', {}, _('未选择（请到「宿舍管理」页设置）'))));

			/* 账号层（1.2.0）：账号字段在 rooms[] 的每个房间对象里（单房间时取那一条），
			   顶层字段缺失就整行不显示 —— 兼容旧引擎。 */
			var acctRow = '';
			if (s.rooms && s.rooms.length == 1)
				acctRow = accountText(s.rooms[0]);
			else if (s.mail_account !== undefined || s.notify_account !== undefined)
				acctRow = accountText(s);
			if (acctRow)
				nodes.push(row(_('账号 / 通道'), acctRow));

			nodes.push(row(_('余额'), E('span', {}, [
				E('span', { 'style': 'font-size:2em;font-weight:700;color:%s'.format(color) }, num(s.balance)),
				E('span', { 'style': 'font-size:0.7em;font-weight:400;color:#5f6368' }, ' ' + unit)
			])));

			nodes.push(row(_('档位'), E('span', {}, [
				levelBadge(level),
				' ',
				E('span', { 'class': 'cbi-value-description' },
					_('低于 %s %s 算不足，低于 %s %s 算预警')
						.format(num(s.threshold, 0), unit, num((s.threshold || 0) * (s.warn_ratio || 1), 0), unit))
			])));

			nodes.push(row(_('日均用量'), qty(s.daily, 2, '%s/%s'.format(unit, _('天')))));
			nodes.push(row(_('预计可用'), qty(s.days_left, 1, _('天'))));
			nodes.push(row(_('上次查询'), fmtTime(s.last_ok_at)));

			nodes.push(row(_('上次提醒'), (s.last_alert_at && Number(s.last_alert_at) > 0)
				? E('span', {}, [
					fmtTime(s.last_alert_at),
					' ',
					E('span', { 'class': 'cbi-value-description' }, _('原因：%s').format(reasonText(s.last_alert_reason)))
				])
				: dash()));

			nodes.push(row(_('累计查询'), (s.poll_count === null || s.poll_count === undefined)
				? dash()
				: '%s %s'.format(s.poll_count, _('次'))));
		}

		nodes.push(row(_('自动查询间隔'), qty(s.interval, 0, _('秒'))));

		if (s.active_hours !== undefined) {
			var ah = s.active_hours
				? E('span', {}, [
					s.active_hours,
					' ',
					(s.in_active_window
						? badge(_('现在在时段内'), LEVEL_COLOR.ok)
						: badge(_('现在不在时段内（暂停查询）'), LEVEL_COLOR.unknown)),
					(s.active_hours_valid === false)
						? ' ' + E('span', { 'style': 'color:%s'.format(LEVEL_COLOR.low) },
							_('格式无效，已按全天处理'))
						: ''
				])
				: E('span', {}, [ _('全天（未设置 active_hours）'), ' ' ]);
			nodes.push(row(_('查询时段'), ah));
		}

		if (s.api_url !== undefined)
			nodes.push(row(_('查询接口'), E('span', { 'style': 'word-break:break-all' },
				s.api_url || E('em', {}, _('未配置（请到「设置」页填写 api.url）')))));

		dom.content(this.statusNode, nodes);
	},

	fillMail: function() {
		var s = this.status;
		if (!s) {
			dom.content(this.mailNode, E('p', {}, E('em', {}, _('读取中…'))));
			return;
		}

		var nodes = [ E('h3', {}, _('邮件提醒通道')) ];

		nodes.push(row(_('状态'), E('span', {}, [
			s.mail_enabled ? badge(_('已启用'), LEVEL_COLOR.ok) : badge(_('未启用'), LEVEL_COLOR.unknown),
			' ',
			s.mail_enabled
				? (s.mail_ready
					? E('span', { 'class': 'cbi-value-description' }, _('投递方式可用'))
					: E('span', { 'style': 'color:%s'.format(LEVEL_COLOR.low) }, _('没有可用的投递方式（需要 python3 或 msmtp）')))
				: E('span', { 'class': 'cbi-value-description' }, [
					_('启用并填好收件人后，余额不足/恢复时会发邮件。'),
					' ',
					E('a', { 'href': L.url('admin/services/powerfee/settings') }, _('去设置'))
				])
		])));

		nodes.push(row(_('收件人'), s.mail_to || dash()));
		nodes.push(row(_('SMTP 服务器'), s.mail_host
			? '%s:%s（%s）'.format(s.mail_host, num(s.mail_port, 0), s.mail_security || dash())
			: dash()));
		nodes.push(row(_('投递方式'), s.mail_transport
			? '%s（%s）'.format(s.mail_transport, s.mail_transport === 'msmtp' ? _('msmtp 命令') : _('python3 内置 SMTP 库'))
			: E('span', { 'style': 'color:%s'.format(LEVEL_COLOR.low) }, _('不可用'))));

		/* 通知推送（webhook）：与邮件互相独立的第二条通道 */
		if (s.notify_enabled !== undefined) {
			nodes.push(row(_('通知推送'), E('span', {}, [
				s.notify_enabled ? badge(_('已启用'), LEVEL_COLOR.ok) : badge(_('未启用'), LEVEL_COLOR.unknown),
				' ',
				E('span', { 'class': 'cbi-value-description' }, [
					s.notify_enabled
						? _('webhook（%s）').format(s.notify_method || 'POST')
						: _('除邮件之外的第二条通知通道，可在「设置」页开启。'),
					s.http_enabled ? ' ' + _('查询端点已开启。') : '',
					' ',
					E('a', { 'href': L.url('admin/services/powerfee/settings') }, _('去设置'))
				])
			])));
		}

		dom.content(this.mailNode, nodes);
	},

	fillService: function() {
		var s = this.status;
		var svc = this.svc || {};

		var running = (typeof svc.running === 'boolean') ? svc.running
			: (s ? s.service_running : null);

		var nodes = [ E('h3', {}, _('后台服务')) ];

		nodes.push(row(_('状态'), (running === null)
			? dash()
			: (running ? badge(_('运行中'), LEVEL_COLOR.ok) : badge(_('已停止'), LEVEL_COLOR.low))));

		nodes.push(row(_('操作'), E('div', {}, [
			E('button', {
				'class': 'cbi-button cbi-button-apply',
				'click': ui.createHandlerFn(this, 'handleService', 'start')
			}, _('启动')),
			' ',
			E('button', {
				'class': 'cbi-button cbi-button-reset',
				'click': ui.createHandlerFn(this, 'handleService', 'stop')
			}, _('停止')),
			' ',
			E('button', {
				'class': 'cbi-button cbi-button-neutral',
				'click': ui.createHandlerFn(this, 'handleService', 'restart')
			}, _('重启'))
		])));

		nodes.push(E('p', { 'class': 'cbi-value-description' },
			_('服务由 procd 常驻（/etc/init.d/powerfee）。改「设置」页的配置不需要重启服务，下一轮查询自动生效。')));

		dom.content(this.serviceNode, nodes);
	},

	/* ---------- 最近 7 天用电量（小卡片） ---------- */

	/* 取 powerfee json usage --days 7 的 summary：合计 / 日均 + 去「用电量」页的链接。
	   单独异步取（逐日接口可能要几十秒），拿不到就整块不显示 —— 旧引擎没有 usage
	   子命令时这一块只是不出现，不影响页面其它部分。
	   注意：刻意不进 refresh()（15 秒轮询），否则每次轮询都会真的请求学校逐日接口。 */
	fillUsage: function() {
		var self = this;
		return pfJson([ 'json', 'usage', '--days', '7' ]).then(function(u) {
			self.usage = u;
			self.paintUsage();
		}, function() {
			self.usage = null;
			self.paintUsage();
		});
	},

	paintUsage: function() {
		var u = this.usage;
		var s = (u && u.ok === true) ? u.summary : null;

		if (!s || !s.count) {
			dom.content(this.usageNode, '');     /* 拿不到数据：整块不显示 */
			return;
		}

		var unit = String(u.unit || (this.status && this.status.unit) || _('度'));
		var parts = [
			_('最近 7 天合计 %s %s').format(num(s.total), unit),
			_('日均 %s %s').format(num(s.avg), unit)
		];
		if (s.missing)
			parts.push(_('缺 %s 天').format(s.missing));

		dom.content(this.usageNode, [
			E('h3', {}, _('用电量')),
			E('p', {}, [
				parts.join(' ｜ '),
				' ',
				E('span', { 'class': 'cbi-value-description' },
					_('%s ~ %s ｜ 来源：%s').format(u.from || dash(), u.to || dash(),
						u.source_text || u.source || _('未知来源')))
			]),
			E('p', {}, E('a', {
				'class': 'btn cbi-button',
				'href': L.url('admin/services/powerfee/usage')
			}, _('查看曲线与逐日数据')))
		]);
	},

	/* ---------- 按钮动作 ---------- */

	handleService: function() {
		var action = handlerArgs(arguments)[0];
		var self = this;
		var label = ACTION_TEXT[action] || action;

		return withBusy(_('正在%s服务…').format(label), fs.exec(PF_INIT, [ action ])).then(function(res) {
			var out = ((res.stdout || '') + (res.stderr || '')).trim();
			if (res.code !== 0)
				throw new Error(out || _('退出码 %s').format(res.code));
			ui.addNotification(null, E('p', {}, _('服务「%s」命令已执行。').format(label) + (out ? ' ' + out : '')), 'success');
			return self.refresh();
		}).catch(notifyError);
	},

	handleCheck: function(ev) {
		var self = this;

		if (!this.status || !this.status.configured) {
			ui.addNotification(null, E('p', {}, _('还没有选择宿舍房间，请先到「宿舍管理」页设置。')), 'warning');
			return;
		}

		return withBusy(_('正在查询余额…'), pfJson([ 'json', 'check' ])).then(function(res) {
			if (res.skipped) {
				ui.addNotification(null, E('div', {}, [
					E('h4', {}, _('现在不在查询时段，已跳过')),
					E('p', {}, _('当前查询时段是 %s，窗口内不发请求。要立刻查一次，请到命令行执行 powerfee check --force。')
						.format(res.active_hours || dash()))
				]), 'warning');
			}
			else if (!res.ok) {
				ui.addNotification(null, E('p', {},
					_('查询失败：%s').format(res.error || res.reason || _('原因未知（详见日志）'))), 'danger');
			}
			else {
				var color = LEVEL_COLOR[res.level] || LEVEL_COLOR.unknown;
				ui.addNotification(null, E('p', {}, [
					_('查询成功：余额 '),
					E('strong', { 'style': 'color:%s'.format(color) }, num(res.balance)),
					' ' + unitOf(self.status),
					_('（%s）').format(LEVEL_TEXT[res.level] || res.level || _('未知'))
				]), 'success');
			}
			return self.refresh();
		}).catch(notifyError);
	},

	handleTestMail: function(ev) {
		var self = this;
		var s = this.status || {};

		if (!s.mail_enabled) {
			ui.addNotification(null, E('p', {}, [
				_('邮件提醒未启用，请先到「设置」页开启并填写收件人与 SMTP 信息。'),
				' ',
				E('a', { 'href': L.url('admin/services/powerfee/settings') }, _('去设置'))
			]), 'warning');
			return;
		}

		if (!s.mail_to) {
			ui.addNotification(null, E('p', {}, [
				_('未配置收件人（mail.to），请先到「设置」页填写。'),
				' ',
				E('a', { 'href': L.url('admin/services/powerfee/settings') }, _('去设置'))
			]), 'warning');
			return;
		}

		return withBusy(_('正在发送测试邮件…'), pfJson([ 'json', 'test-mail' ])).then(function(res) {
			if (res.ok)
				ui.addNotification(null, E('p', {},
					_('测试邮件已发送：%s').format(res.message || s.mail_to)), 'success');
			else
				ui.addNotification(null, E('p', {},
					_('测试邮件发送失败：%s').format(res.message || _('原因见运行日志'))), 'danger');
			return self.refresh();
		}).catch(notifyError);
	},

	handleSelftest: function(ev) {
		return withBusy(_('正在运行自检…'), pfJson([ 'json', 'selftest' ])).then(function(res) {
			var passed = (res.passed === null || res.passed === undefined) ? 0 : res.passed;
			var failed = (res.failed === null || res.failed === undefined) ? 0 : res.failed;

			if (res.ok)
				ui.addNotification(null, E('p', {}, _('自检通过：%s 项全部正常。').format(passed)), 'success');
			else
				ui.addNotification(null, E('p', {}, _('自检失败：通过 %s 项，失败 %s 项。').format(passed, failed)), 'danger');
		}).catch(notifyError);
	},

	handleRefreshLog: function(ev) {
		var self = this;
		return pfExec([ 'log', '40' ]).then(function(out) {
			dom.content(self.logPre, out || _('（日志为空）'));
		}).catch(notifyError);
	}
});
