'use strict';
'require view';
'require fs';
'require ui';
'require dom';

/*
 * 宿舍电量哨兵 · 用电量页
 *
 * 逐日用电量曲线 + 数据表 + 「立刻发一份报告」。
 * 数据全部来自主程序 /usr/bin/powerfee 的机器可读接口：
 *   powerfee json room-list                     已配置的房间（选择器用）
 *   powerfee json status                        当前状态（单位等兜底信息）
 *   powerfee json usage [房间] [--days N] [--month YYYY-MM] [--png]
 *        -> {ok, source, unit, empty, from, to,
 *            days:[{date, used, incomplete}],
 *            summary:{total, avg, max, max_date, min, min_date, count, missing, incomplete},
 *            text, summary_line, source_text[, png_base64]}
 *        source = api（学校逐日接口）/ estimate（本地采样估算，接口不可用时的兜底）；
 *        取数失败也回合法 JSON：{ok:false, error, text, days:[]}。
 *   powerfee report [房间] [--days N]            立刻发一份报告（邮件 + 推送，走房间自己的账号）
 *
 * 曲线用后端渲染的 PNG（png_base64，640×240，与报告邮件里的图完全同源），
 * 拿不到 PNG 时退回后端给的文本曲线（res.text）；逐日数据表始终在下面兜底。
 * 刻意不轮询：每次取数都会真的请求学校逐日接口（当月数据每次重取），
 * 所以只有点「刷新」/ 换房间 / 换范围时才取一次。
 */

var PF_BIN = '/usr/bin/powerfee';

var COLOR = { ok: '#188038', warn: '#e8710a', err: '#d93025', dim: '#5f6368', link: '#1a73e8' };

var STYLE = '' +
	'.powerfee-usage .cbi-value-title{width:22%}' +
	'.powerfee-usage .pf-usage-chart img{max-width:100%;height:auto;border:1px solid #dadce0;' +
	'border-radius:4px;background:#fff}' +
	'.powerfee-usage .pf-usage-text{max-height:320px;overflow:auto;background:#f8f9fa;border:1px solid #dadce0;' +
	'border-radius:4px;padding:10px;font-family:monospace;font-size:12px;white-space:pre;margin:0}' +
	'.powerfee-usage .pf-usage-badge{display:inline-block;padding:2px 10px;border-radius:10px;color:#fff;font-size:12px}' +
	'.powerfee-usage .pf-usage-bar{margin:.2em 0}' +
	'.powerfee-usage .pf-usage-bar select,.powerfee-usage .pf-usage-bar input{margin-right:6px}';

/* 范围选择：值与 json usage 的 --days 一致；month = 指定自然月 */
var RANGES = [
	{ value: '7', title: _('最近 7 天') },
	{ value: '14', title: _('最近 14 天') },
	{ value: '30', title: _('最近 30 天') },
	{ value: 'month', title: _('指定月份（YYYY-MM）') }
];

/* ---------- 小工具 ---------- */

function dash() {
	return '—';
}

function num(v, digits) {
	if (v === null || v === undefined || v === '' || isNaN(v))
		return dash();
	return Number(v).toFixed(digits == null ? 2 : digits);
}

function pad2(n) {
	return (n < 10 ? '0' : '') + n;
}

function monthNow() {
	var d = new Date();
	return '%d-%s'.format(d.getFullYear(), pad2(d.getMonth() + 1));
}

/* 日期只显示 MM-DD（表里显示完整日期） */
function shortDate(s) {
	s = String(s || '');
	return s.length >= 10 ? s.slice(5) : (s || dash());
}

/* 房间展示名：优先 display，其次 label / 编号 / 段名 */
function roomLabel(r) {
	if (!r)
		return '';
	return String(r.display || r.label || r.num || r.id || r.section || '').trim();
}

/* 数据来源的中文名：优先用后端回显的 source_text（与邮件里同一份话术） */
function sourceText(res) {
	if (res && res.source_text)
		return String(res.source_text);
	if (res && res.source === 'api')
		return _('学校接口逐日数据');
	if (res && res.source === 'estimate')
		return _('本地采样估算');
	return _('未知来源');
}

function badge(text, color) {
	return E('span', { 'class': 'pf-usage-badge', 'style': 'background:%s'.format(color || COLOR.dim) }, text);
}

function row(label, value) {
	return E('div', { 'class': 'cbi-value' }, [
		E('label', { 'class': 'cbi-value-title' }, label),
		E('div', { 'class': 'cbi-value-field' }, value)
	]);
}

/* 跑一条 powerfee 命令：stdout 是 JSON 就解析（退出码非 0 也照样解析 ——
   json usage 在「参数不对 / 无数据」时就是「退出码 1 + 合法 JSON」）。 */
function pfExecJson(args) {
	return fs.exec(PF_BIN, args).then(function(res) {
		var out = (res.stdout || '').trim();
		if (out) {
			try {
				return JSON.parse(out);
			}
			catch (e) {
				/* 落到下面统一报错 */
			}
		}
		var msg = ((res.stderr || '') + (res.stdout || '')).trim();
		throw new Error(msg || _('powerfee 执行失败（退出码 %s）').format(res.code));
	});
}

/* 同上，但失败变成 {error: '...'}，页面直接显示原因，不崩 */
function pfJsonSafe(args) {
	return pfExecJson(args).catch(function(e) {
		return { error: (e && e.message) ? e.message : String(e) };
	});
}

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
	handleSave: null,
	handleSaveApply: null,
	handleReset: null,

	load: function() {
		return Promise.all([
			pfJsonSafe([ 'json', 'room-list' ]),
			pfJsonSafe([ 'json', 'status' ])
		]);
	},

	render: function(data) {
		var self = this;

		this.roomsData = data[0] || {};
		this.status = (data[1] && !data[1].error) ? data[1] : {};
		this.rooms = Array.isArray(this.roomsData.rooms) ? this.roomsData.rooms : [];
		this.multi = this.rooms.length > 1;   /* 单房间（含旧版单房间模式）不传房间参数 */
		this.usage = null;
		this.usageError = null;

		/* --- 查询条件：房间 + 范围 + 刷新 --- */
		this.roomSel = E('select', {
			'class': 'cbi-input-select',
			'change': ui.createHandlerFn(this, 'handleRoomChange')
		});
		this.fillRooms();

		this.rangeSel = E('select', {
			'class': 'cbi-input-select',
			'change': ui.createHandlerFn(this, 'handleRangeChange')
		});
		RANGES.forEach(function(r) {
			self.rangeSel.appendChild(E('option', { 'value': r.value }, r.title));
		});
		this.rangeSel.value = '7';

		this.monthInput = E('input', {
			'type': 'month',
			'class': 'cbi-input-text',
			'value': monthNow(),
			'placeholder': 'YYYY-MM',
			'change': ui.createHandlerFn(this, 'handleRangeChange')
		});
		this.monthWrap = E('span', { 'style': 'display:none' }, [
			this.monthInput,
			E('span', { 'class': 'cbi-value-description' }, _('（自然月，例如 2026-09）'))
		]);

		/* --- 各区域 --- */
		this.noticeNode = E('div');
		this.chartNode = E('div', { 'class': 'pf-usage-chart' });
		this.summaryNode = E('div');
		this.tableNode = E('div');

		var view = E('div', { 'class': 'powerfee-usage' }, [
			E('style', {}, STYLE),
			E('h2', {}, _('用电量')),

			E('div', { 'class': 'cbi-section' }, this.noticeNode),

			E('div', { 'class': 'cbi-section' }, [
				E('h3', {}, _('查询')),
				E('div', { 'class': 'pf-usage-bar' }, [
					E('label', { 'style': 'margin-right:6px' }, _('房间')),
					this.roomSel,
					E('label', { 'style': 'margin:0 6px 0 12px' }, _('范围')),
					this.rangeSel,
					this.monthWrap,
					' ',
					E('button', {
						'class': 'cbi-button cbi-button-reload',
						'click': ui.createHandlerFn(this, 'handleRefresh')
					}, _('刷新'))
				]),
				E('p', { 'class': 'cbi-value-description' },
					_('曲线与逐日数据来自「逐日用电量接口」（设置页可配）。当月数据每次刷新都会重新取；' +
					  '历史月走本地缓存，不再重复请求。接口没配或取不到时会退回本地采样估算（仅供参考）。'))
			]),

			E('div', { 'class': 'cbi-section' }, [
				E('h3', {}, _('逐日用电量曲线')),
				this.chartNode,
				this.summaryNode
			]),

			E('div', { 'class': 'cbi-section' }, [
				E('h3', {}, _('逐日数据')),
				this.tableNode
			]),

			E('div', { 'class': 'cbi-section' }, [
				E('h3', {}, _('发送报告')),
				E('p', { 'class': 'cbi-value-description' },
					_('点下面的按钮会立刻发一份「今日用量 + 最近 N 天曲线」的报告：邮件（带图）与推送各走这个房间自己的' +
					  '收件人 / 发件账号 / 推送通道，与余额告警同一套账号。N 用当前选的 7/14/30 天；' +
					  '选「指定月份」时按设置页的「报告天数」发（默认 7 天）。')),
				E('p', {}, E('button', {
					'class': 'cbi-button cbi-button-action important',
					'click': ui.createHandlerFn(this, 'handleReport')
				}, _('立刻发一份报告')))
			])
		]);

		this.paintNotice();
		this.refreshUsage();    /* 先把各区域画成「读取中…」，取完数再重画 */

		return view;
	},

	/* ---------- 查询条件 ---------- */

	fillRooms: function() {
		var self = this;

		dom.content(this.roomSel, '');

		if (!this.rooms.length) {
			/* 没有房间（或读不到房间列表）：不传房间参数，主程序用默认房间 / 报「还没设置房间」 */
			this.roomSel.appendChild(E('option', { 'value': '' }, _('（未配置房间）')));
			this.roomSel.disabled = true;
			return;
		}

		var firstEnabled = null;
		this.rooms.forEach(function(r) {
			if (!firstEnabled && r.enabled !== false)
				firstEnabled = r;
		});
		var pick = firstEnabled || this.rooms[0];

		this.rooms.forEach(function(r) {
			var label = roomLabel(r) || _('（未命名）');
			if (r.enabled === false)
				label += ' ' + _('（已停用）');
			if (r.missing_num)
				label += ' ' + _('（配置不完整）');
			self.roomSel.appendChild(E('option', {
				'value': String(r.id || r.section || ''),
				'selected': (pick === r) ? '' : null
			}, label));
		});

		/* 单房间：选择器只有一项，禁用（避免让人以为能切） */
		this.roomSel.disabled = !this.multi;
	},

	/* 选中的房间参数：多房间才传（单房间 / 旧版模式不传，让主程序用默认房间） */
	selectedRoomArg: function() {
		if (!this.multi)
			return '';
		return String(this.roomSel.value || '');
	},

	handleRoomChange: function(ev) {
		this.refreshUsage();
	},

	handleRangeChange: function(ev) {
		var rng = String(this.rangeSel.value || '7');
		this.monthWrap.style.display = (rng === 'month') ? 'inline' : 'none';
		/* 切到「指定月份」时月份框里已经填好当前月，直接取一次；改月份也会再取一次 */
		return this.refreshUsage();
	},

	handleRefresh: function(ev) {
		return this.refreshUsage();
	},

	/* ---------- 取数 ---------- */

	/* 组装 json usage 的参数；月份格式不对时返回 null（并提示） */
	usageArgs: function(withPng) {
		var args = [ 'json', 'usage' ];
		var room = this.selectedRoomArg();
		if (room)
			args.push(room);

		var rng = String(this.rangeSel.value || '7');
		if (rng === 'month') {
			var m = String(this.monthInput.value || '').trim();
			if (!/^[0-9]{4}-[0-9]{2}$/.test(m)) {
				ui.addNotification(null, E('p', {}, _('月份格式不对，请用 YYYY-MM（例如 2026-09）。')), 'warning');
				return null;
			}
			args.push('--month', m);
		}
		else {
			args.push('--days', rng);
		}
		if (withPng)
			args.push('--png');
		return args;
	},

	refreshUsage: function() {
		var self = this;
		var args = this.usageArgs(true);
		if (!args)
			return Promise.resolve();

		dom.content(this.chartNode, E('p', { 'class': 'spinning' }, _('正在读取用电量…')));
		dom.content(this.summaryNode, '');
		dom.content(this.tableNode, E('p', {}, E('em', {}, _('读取中…'))));

		return pfExecJson(args).then(function(res) {
			self.usage = res;
			self.usageError = null;
		}, function(err) {
			self.usage = null;
			self.usageError = err;
		}).then(function() {
			self.paintUsage();
		});
	},

	/* ---------- 渲染 ---------- */

	paintNotice: function() {
		var nodes = [];
		var s = this.status || {};

		if (this.roomsData && this.roomsData.error) {
			nodes.push(E('div', { 'class': 'alert-message warning' }, [
				E('h4', {}, _('读不到房间列表')),
				E('pre', { 'style': 'white-space:pre-wrap;margin:0' }, String(this.roomsData.error)),
				E('p', {}, _('这里仍然会显示默认房间的用电量；如果页面一直取不到数据，多半是 powerfee 还是旧版本（没有 usage 子命令）。'))
			]));
		}

		if (!this.rooms.length && s.configured === false) {
			nodes.push(E('div', { 'class': 'alert-message warning' }, [
				E('h4', {}, _('还没有选择要监控的宿舍')),
				E('p', {}, [
					_('请先到「宿舍管理」页选定房间，这里才有用电量可看。'),
					' ',
					E('a', { 'class': 'btn cbi-button cbi-button-action', 'href': L.url('admin/services/powerfee/room') }, _('去管理宿舍'))
				])
			]));
		}

		dom.content(this.noticeNode, nodes);
	},

	paintUsage: function() {
		var res = this.usage;
		var err = this.usageError;

		/* --- 取数失败（旧引擎 / 没有 python3 / 权限等） --- */
		if (err) {
			dom.content(this.chartNode, this.alertNodes(_('读取用电量失败'),
				(err && err.message) ? err.message : String(err),
				_('请确认 /usr/bin/powerfee 已安装且 ACL（/usr/share/rpcd/acl.d/luci-app-powerfee.json）已生效。')));
			dom.content(this.summaryNode, '');
			dom.content(this.tableNode, '');
			return;
		}

		if (!res || res.ok !== true) {
			/* 参数不对 / 没设置房间 / 逐日接口与本地采样都没有数据 */
			var detail = (res && res.error) ? String(res.error) : _('这个时间段内没有可用的用电量数据。');
			var hint = E('span', {}, [
				_('可能的原因：还没有配置逐日用电量接口（设置页的 api.daily_url），' +
				  '本地采样也还不够（服务需要先跑一段时间）。'),
				' ',
				E('a', { 'href': L.url('admin/services/powerfee/settings') }, _('去设置逐日接口'))
			]);
			dom.content(this.chartNode, this.alertNodes(_('没有用电量数据'), detail, hint, 'warning'));
			dom.content(this.summaryNode, '');
			dom.content(this.tableNode, '');
			return;
		}

		var unit = String(res.unit || (this.status && this.status.unit) || _('度'));
		var sum = res.summary || {};

		/* --- 曲线：优先后端渲染的 PNG（与邮件里的图同源） --- */
		var chart = [];
		if (res.png_base64) {
			chart.push(E('img', {
				'src': 'data:image/png;base64,' + String(res.png_base64),
				'alt': _('逐日用电量曲线'),
				'title': '%s — %s'.format(res.from || '', res.to || '')
			}));
		}
		else if (res.text) {
			chart.push(E('pre', { 'class': 'pf-usage-text' }, String(res.text)));
		}
		else {
			chart.push(E('p', {}, E('em', {}, _('（后端没有返回曲线图）'))));
		}
		dom.content(this.chartNode, chart);

		/* --- 摘要 --- */
		var summaryNodes = [];
		summaryNodes.push(row(_('数据来源'), E('span', {}, [
			res.source === 'api'
				? badge(_('学校接口逐日数据'), COLOR.ok)
				: (res.source === 'estimate'
					? badge(_('本地采样估算'), COLOR.warn)
					: badge(sourceText(res), COLOR.dim)),
			' ',
			E('span', { 'class': 'cbi-value-description' },
				res.source === 'estimate'
					? _('接口不可用时的兜底估算（按余额变化归日），仅供参考。')
					: _('来自学校逐日接口，与报告邮件里的曲线同一份数据。'))
		])));
		summaryNodes.push(row(_('时间范围'), '%s ~ %s'.format(res.from || dash(), res.to || dash())));
		summaryNodes.push(row(_('合计'), '%s %s'.format(num(sum.total), unit)));
		summaryNodes.push(row(_('日均'), '%s %s/%s'.format(num(sum.avg), unit, _('天'))));
		summaryNodes.push(row(_('最高'), '%s %s（%s）'.format(num(sum.max), unit, shortDate(sum.max_date))));
		summaryNodes.push(row(_('最低'), '%s %s（%s）'.format(num(sum.min), unit, shortDate(sum.min_date))));

		var statParts = [ _('%s 天有数据').format(sum.count === null || sum.count === undefined ? 0 : sum.count) ];
		if (sum.missing)
			statParts.push(_('缺 %s 天').format(sum.missing));
		if (sum.incomplete)
			statParts.push(_('%s 天采样不完整').format(sum.incomplete));
		summaryNodes.push(row(_('统计'), statParts.join(' ｜ ')));
		dom.content(this.summaryNode, summaryNodes);

		/* --- 逐日数据表 --- */
		var days = Array.isArray(res.days) ? res.days : [];
		var rows = [ E('tr', { 'class': 'tr table-titles' }, [
			E('th', {}, _('日期')),
			E('th', { 'class': 'right' }, _('用量（%s）').format(unit)),
			E('th', {}, _('备注'))
		]) ];

		days.forEach(function(d) {
			var note = '';
			if (d.used === null || d.used === undefined)
				note = E('span', { 'style': 'color:%s'.format(COLOR.err) }, _('缺数据'));
			else if (d.incomplete)
				note = E('span', { 'style': 'color:%s'.format(COLOR.warn) }, _('采样不完整，仅供参考'));
			rows.push(E('tr', { 'class': 'tr' }, [
				E('td', {}, String(d.date || '')),
				E('td', { 'class': 'right' }, num(d.used)),
				E('td', {}, note)
			]));
		});

		dom.content(this.tableNode, [
			E('div', { 'style': 'max-height:420px;overflow:auto' }, E('table', { 'class': 'table' }, rows)),
			E('p', { 'class': 'cbi-value-description' },
				_('「缺数据」= 这天接口/采样都没有记录（曲线在此断开，不猜不补）；' +
				  '「采样不完整」= 本地采样当天有缺口，估算值可能偏小。'))
		]);
	},

	/* 统一的提示块 */
	alertNodes: function(title, message, hint, kind) {
		var box = [
			E('h4', {}, title),
			E('pre', { 'style': 'white-space:pre-wrap;margin:0' }, message)
		];
		if (hint)
			box.push(E('p', {}, hint));
		return E('div', { 'class': 'alert-message ' + (kind || 'danger') }, box);
	},

	/* ---------- 立刻发一份报告 ---------- */

	handleReport: function(ev) {
		var args = [ 'report' ];
		var room = this.selectedRoomArg();
		if (room)
			args.push(room);

		var rng = String(this.rangeSel.value || '7');
		if (rng !== 'month')
			args.push('--days', rng);

		if (!this.rooms.length && this.status && this.status.configured === false) {
			ui.addNotification(null, E('p', {}, [
				_('还没有配置监控房间，报告发不出去。'),
				' ',
				E('a', { 'href': L.url('admin/services/powerfee/room') }, _('去管理宿舍'))
			]), 'warning');
			return Promise.resolve();
		}

		return withBusy(_('正在发送报告…'), fs.exec(PF_BIN, args)).then(function(res) {
			var out = ((res.stdout || '') + (res.stderr || '')).trim();

			if (res.code === 0) {
				ui.addNotification(null, E('div', {}, [
					E('p', {}, _('报告已发出。')),
					E('p', { 'class': 'cbi-value-description' },
						_('邮件与推送各走这个房间自己的收件人 / 发件账号 / 推送通道。' +
						  '没收到就检查「设置 → 邮件提醒 / 通知推送」以及房间的账号设置；运行日志里会写明每个通道发没发出去。'))
				]), 'success');
			}
			else {
				ui.addNotification(null, E('div', {}, [
					E('p', {}, _('报告没有发出去：%s').format(out || _('（没有更多输出，详见运行日志）'))),
					E('p', { 'class': 'cbi-value-description' },
						_('常见原因：邮件未启用（mail.enabled=0）或没有收件人；推送未启用或没有地址。' +
						  '到「设置」页检查这两条通道，或到「状态」页看运行日志。'))
				]), 'danger');
			}
		}).catch(notifyError);
	}
});
