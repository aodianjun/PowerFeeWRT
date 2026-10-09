'use strict';
'require view';
'require fs';
'require ui';
'require dom';

/*
 * 宿舍电量哨兵 · 宿舍管理页（多房间）
 *
 * 一个路由器可以同时监控多个房间，每个房间有自己的收件邮箱与推送通道，
 * 1.2.0 起还能各自指定「发件账号」（mail_account）与「推送账号」（notify_account）。
 * 数据来源（主程序 powerfee 的机器可读接口，房间/校区/楼栋名全部来自接口返回值）：
 *   powerfee json room-list              已配置的房间（含余额/档位/收件人/推送通道，密钥只回掩码）
 *   powerfee json groups                 校区 -> 楼栋（级联下拉）
 *   powerfee json rooms [关键词] [条数]   学校接口返回的房间列表（搜索/浏览）
 *   powerfee json mail-accounts          命名发件账号清单（config mail_account，密钥只回掩码）
 *   powerfee json push-accounts          命名推送账号清单（config push_account，密钥只回掩码）
 *   powerfee json room-add <房间号>       添加一个房间（自动填好校区/楼栋/房间名，并继承默认收件人/通道）
 *   powerfee json room-add --batch <编号…> 一次添加多个房间（只请求接口一次；返回 results[] 逐房间结果，
 *                                        已监控的按编号跳过，部分失败不整体失败）
 *   powerfee json room-save <段名> k=v…   批量修改一个房间（只 commit 一次）
 *   powerfee json room-remove <段名>      删除一个房间（连同它的状态与历史）
 *
 * 说明：还没有 config room 段时是「单房间模式」（沿用 main.room_num 与 state / history.csv）；
 * 添加第一个房间时，主程序会把原来的状态与历史继承给它（编号相同才继承），不会丢历史。
 */

var PF_BIN = '/usr/bin/powerfee';
var SEARCH_LIMIT = '200';   /* 房间号搜索结果上限 */
var BROWSE_LIMIT = '500';   /* 按楼栋浏览时服务端一次取回的条数上限 */
var ALL_LIMIT = '2100';     /* 「加载全部房间」的条数上限 */

var LEVEL_COLOR = {
	ok: '#188038',
	warn: '#e8710a',
	low: '#d93025',
	unknown: '#5f6368'
};

var LEVEL_TEXT = {
	ok: _('充足'),
	warn: _('预警'),
	low: _('不足'),
	unknown: _('未知')
};

var STYLE = '' +
	'.powerfee-room .cbi-value-title{width:22%}' +
	'.powerfee-room table.table td,.powerfee-room table.table th{padding:6px 8px}' +
	'.powerfee-room tr.powerfee-picked{background:#e8f0fe}' +
	'.powerfee-room .powerfee-scroll{max-height:420px;overflow:auto}' +
	'.powerfee-room .powerfee-muted{color:#5f6368}';

/* ---------- 小工具 ---------- */

function dash() {
	return '—';
}

/* 电量单位：优先用主程序返回的 unit，取不到就回退「度」 */
function unitOf(s) {
	return (s && s.unit) ? String(s.unit) : _('度');
}

/* 房间唯一编号：新版本接口用 id，旧版本用 roomNum，两个都认 */
function roomId(r) {
	if (!r)
		return '';
	if (r.id !== null && r.id !== undefined && r.id !== '')
		return String(r.id);
	if (r.roomNum !== null && r.roomNum !== undefined && r.roomNum !== '')
		return String(r.roomNum);
	return '';
}

/* 多选用的键：优先房间编号（与主程序批量匹配的顺序一致），没有编号时退回房间名 */
function selKey(r) {
	return roomId(r) || String((r && r.room) || '');
}

/* 搜索结果里的「已监控」标记（title 里带上它在哪个 config 段） */
function monitoredBadge(section) {
	return E('span', {
		'class': 'powerfee-badge',
		'title': section ? _('已经在监控里（config room %s），不会被重复添加').format(section)
			: _('已经在监控里，不会被重复添加'),
		'style': 'display:inline-block;padding:1px 8px;border-radius:9px;color:#fff;' +
			'font-size:11px;background:#5f6368;margin-left:6px;vertical-align:middle'
	}, _('已监控'));
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

function levelBadge(level) {
	level = level || 'unknown';
	return E('span', {
		'class': 'powerfee-badge',
		'style': 'display:inline-block;padding:2px 10px;border-radius:10px;color:#fff;font-size:12px;background:%s'
			.format(LEVEL_COLOR[level] || LEVEL_COLOR.unknown)
	}, LEVEL_TEXT[level] || level);
}

function row(label, value) {
	return E('div', { 'class': 'cbi-value' }, [
		E('label', { 'class': 'cbi-value-title' }, label),
		E('div', { 'class': 'cbi-value-field' }, value)
	]);
}

function pfExec(args) {
	return fs.exec(PF_BIN, args).then(function(res) {
		if (res.code !== 0) {
			var msg = ((res.stderr || '') + (res.stdout || '')).trim();
			throw new Error(msg || _('powerfee 执行失败（退出码 %s）').format(res.code));
		}
		return res.stdout || '';
	});
}

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

/* 同 pfJson，但把失败也变成 {error: '...'}，方便在页面上直接显示原因 */
function pfJsonSafe(args) {
	return pfJson(args).then(function(res) {
		return res;
	}, function(e) {
		return { error: (e && e.message) ? e.message : String(e) };
	});
}

/* 同 pfJson，但退出码非 0 时也先尝试解析 stdout。
   批量添加有失败（没找到 / 写配置失败）时主程序会以非 0 退出，
   但 JSON（含逐房间的 results[]）照样在 stdout 上，界面要能拿到它。 */
function pfJsonAny(args) {
	return fs.exec(PF_BIN, args).then(function(res) {
		var out = String(res.stdout || '');
		try {
			return JSON.parse(out);
		}
		catch (e) {
			var msg = ((res.stderr || '') + out).trim();
			return { error: msg || _('powerfee 执行失败（退出码 %s）').format(res.code) };
		}
	}, function(e) {
		return { error: (e && e.message) ? e.message : String(e) };
	});
}

/* ui.createHandlerFn 的参数顺序在新旧 LuCI 里是**反的**：
   老版（≤23.05）传 (事件, …附加参数)，新版（26.x）传 (…附加参数, 事件)。
   这里用「有没有 currentTarget」把事件认出来丢掉，只留附加参数 ——
   两代 LuCI 都不会崩（曾经因为按老顺序写，26.x 上保存房间直接 TypeError）。 */
function handlerArgs(args) {
	var out = [];
	Array.prototype.forEach.call(args, function(x) {
		if (!(x && typeof x === 'object' && 'currentTarget' in x))
			out.push(x);
	});
	return out;
}

/* json mail-accounts / push-accounts 的兜底解析：accounts / list / 裸数组都认 */
function accountList(res) {
	if (!res || res.error)
		return null;
	var list = res.accounts || res.list || null;
	if (!list && Array.isArray(res))
		list = res;
	return Array.isArray(list) ? list : null;
}

/* 账号名清单；null = 读不到（旧版本 / 报错），页面据此只显示「跟随默认」并给提示 */
function accountNames(res) {
	var list = accountList(res);
	if (!list)
		return null;
	return list.map(function(a) {
		var enabled = !(a && (a.enabled === false || a.enabled === '0' || a.enabled === 0));
		return {
			name: String((a && (a.name || a.section || a.id)) || '').trim(),
			enabled: enabled,
			usable: (a && typeof a.usable === 'boolean') ? a.usable : (enabled ? null : false),
			issue: String((a && a.issue) || '')
		};
	}).filter(function(a) {
		return a.name;
	});
}

/* 主程序在「接口未配置 / 查询失败」时会返回带 error 字段的合法 JSON */
function apiError(res) {
	return (res && typeof res.error === 'string' && res.error !== '') ? res.error : null;
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

/* 关键词只保留字母/数字/汉字/连字符，避免拼进 jsonfilter 表达式时出问题 */
function sanitizeKeyword(kw) {
	return String(kw || '').replace(/[^0-9A-Za-z\u4e00-\u9fa5\-]/g, '').slice(0, 32);
}

/* 把 json room-* 的回答转成用户能看懂的提示（失败时把 message 原样显示） */
function notifyResult(res, titleOk) {
	if (!res || !res.ok) {
		ui.addNotification(null, E('div', {}, [
			E('h4', {}, _('操作失败')),
			E('pre', { 'style': 'white-space:pre-wrap;margin:0' },
				(res && res.message) || res.error || _('主程序没有返回失败原因。'))
		]), 'danger');
		return false;
	}
	ui.addNotification(null, E('div', {}, [
		E('h4', {}, titleOk || _('完成')),
		E('pre', { 'style': 'white-space:pre-wrap;margin:0' }, res.message || '')
	]), 'success');
	return true;
}

/* ---------- 页面 ---------- */

return view.extend({
	handleSave: null,
	handleSaveApply: null,
	handleReset: null,

	load: function() {
		return Promise.all([
			pfJsonSafe([ 'json', 'room-list' ]),
			pfJsonSafe([ 'json', 'groups' ]),
			L.resolveDefault(pfJsonSafe([ 'json', 'status' ]), {}),
			pfJsonSafe([ 'json', 'mail-accounts' ]),
			pfJsonSafe([ 'json', 'push-accounts' ])
		]);
	},

	render: function(data) {
		var self = this;

		this.roomsData = data[0] || {};
		this.groups = (data[1] && data[1].groups) || [];
		this.groupsError = apiError(data[1]);
		this.status = data[2] || {};
		this.mailAccounts = accountNames(data[3]);   /* null = 读不到（旧版本 / 报错） */
		this.pushAccounts = accountNames(data[4]);
		this.allRooms = null;   /* 「加载全部房间」后的本地缓存 */
		this.selected = {};     /* 勾选集合：键是 selKey(r)（房间编号），值是房间对象 */
		this.rowMap = {};       /* 键 -> 结果表格里的行记录（tr / 复选框 / 按钮），用于同步勾选状态 */
		this.resultRows = [];   /* 当前列表里的行记录，用于高亮与批量勾选 */
		this.lastResult = null; /* 最近一次 renderRooms 的参数，刷新「已监控」标记时重绘用 */
		this.headCb = null;     /* 表头的「全选本页」复选框 */
		this.pageSelectable = 0;/* 当前显示的行里可选（未监控）的数量 */
		this.buildMonitored();

		this.noticeNode = E('div');
		this.roomsNode = E('div');
		this.resultNode = E('div');
		this.pickNode = E('div');

		var defaultKeyword = this.status.room ? String(this.status.room) : '';

		this.searchInput = E('input', {
			'type': 'text',
			'class': 'cbi-input-text',
			'style': 'width:14em',
			'placeholder': _('房间号 / 关键词'),
			'value': defaultKeyword,
			'keydown': function(ev) {
				if (ev.key === 'Enter') {
					ev.preventDefault();
					self.handleSearch();
				}
			}
		});

		this.campusSel = E('select', {
			'class': 'cbi-input-select',
			'change': function(ev) {
				self.fillBuildings(ev.target.value);
			}
		});

		this.buildingSel = E('select', { 'class': 'cbi-input-select' });

		var view = E('div', { 'class': 'powerfee-room' }, [
			E('style', {}, STYLE),
			E('h2', {}, _('宿舍管理')),

			E('div', { 'class': 'cbi-section' }, this.noticeNode),
			E('div', { 'class': 'cbi-section' }, this.roomsNode),

			E('div', { 'class': 'cbi-section' }, [
				E('h3', {}, _('添加房间')),
				E('p', { 'class': 'cbi-value-description' },
					_('先搜到宿舍，再勾选（或点整行）要监控的房间 —— 一次可以选多个，然后点「添加选中的 N 个」。' +
					  '新房间默认继承「设置」页里的收件人与推送通道，添加后可以在上面的列表里单独修改。')),
				E('p', { 'class': 'cbi-value-description' },
					_('输入完整房间号或其中一部分（门牌上印的号码），回车或点「搜索」。这是最准确的方式。')),
				E('div', {}, [
					this.searchInput,
					' ',
					E('button', {
						'class': 'cbi-button cbi-button-find',
						'click': ui.createHandlerFn(this, 'handleSearch')
					}, _('搜索'))
				])
			]),

			E('div', { 'class': 'cbi-section' }, [
				E('h3', {}, _('按校区 / 楼栋浏览')),
				E('p', { 'class': 'cbi-value-description' },
					_('选择校区与楼栋后点「浏览该楼房间」，会用楼栋名做关键词取回一批，再在本地按校区+楼栋精确筛选。' +
					  '如果某个楼栋查不到房间，请用上面的搜索框，或点「加载全部房间」在本地筛选。')),
				row(_('校区'), this.campusSel),
				row(_('楼栋'), this.buildingSel),
				E('div', {}, [
					E('button', {
						'class': 'cbi-button cbi-button-action',
						'click': ui.createHandlerFn(this, 'handleBrowse')
					}, _('浏览该楼房间')),
					' ',
					E('button', {
						'class': 'cbi-button cbi-button-neutral',
						'click': ui.createHandlerFn(this, 'handleLoadAll')
					}, _('加载全部房间'))
				]),
				E('p', { 'class': 'cbi-value-description' },
					_('「加载全部房间」会一次取回全部房间（数据量取决于学校接口，可能几百 KB），之后浏览/筛选都在本地进行，不再请求接口。'))
			]),

			E('div', { 'class': 'cbi-section' }, this.resultNode),
			E('div', { 'class': 'cbi-section' }, this.pickNode)
		]);

		this.fillNotice();
		this.fillRooms();
		this.fillPick();
		this.fillCampuses();

		return view;
	},

	/* ---------- 顶部提示（接口未配置 / 读取失败） ---------- */

	fillNotice: function() {
		var nodes = [];
		var d = this.roomsData || {};
		var s = this.status || {};

		if (this.groupsError) {
			nodes.push(E('div', { 'class': 'alert-message warning' }, [
				E('h4', {}, _('读取房间清单失败')),
				E('pre', { 'style': 'white-space:pre-wrap;margin:0' }, this.groupsError),
				E('p', {}, [
					_('通常是还没配置电费查询接口（api.url），或接口暂时不可用。'),
					' ',
					E('a', { 'href': L.url('admin/services/powerfee/settings') }, _('去设置里填接口'))
				])
			]));
		}

		if (!d.count && s.room_num) {
			nodes.push(E('div', { 'class': 'alert-message info' }, [
				E('h4', {}, _('当前是单房间模式')),
				E('p', {}, _('还没有「多房间」配置，正在按旧版方式监控一个房间：%s（编号 %s）。')
					.format(s.room_display || dash(), s.room_num)),
				E('p', {}, [
					_('想给多个房间分别设置收件人 / 推送通道，就点下面的按钮把当前房间转成多房间配置' +
					  '（会把它的状态与历史一起继承过去，不会丢数据）：'),
					' ',
					E('button', {
						'class': 'cbi-button cbi-button-action',
						'click': ui.createHandlerFn(this, 'handleAdoptCurrent')
					}, _('把当前房间加入多房间管理'))
				])
			]));
		}

		dom.content(this.noticeNode, nodes);
	},

	/* 列表区域的错误展示 */
	renderError: function(title, message) {
		this.resultRows = [];
		this.rowMap = {};
		this.headCb = null;
		this.lastResult = null;
		this.pageSelectable = 0;
		dom.content(this.resultNode, [
			E('h3', {}, title || _('出错了')),
			E('div', { 'class': 'alert-message danger' }, [
				E('pre', { 'style': 'white-space:pre-wrap;margin:0' }, message),
				E('p', {}, [
					_('如果提示「尚未配置接口」，请到设置页填写接口地址。'),
					' ',
					E('a', { 'href': L.url('admin/services/powerfee/settings') }, _('去设置'))
				])
			])
		]);
	},

	/* ---------- 已配置的房间 ---------- */

	deliveryText: function(r) {
		var parts = [];
		if (r.mail_to)
			parts.push(_('邮件 → %s').format(r.mail_to));
		else
			parts.push(_('邮件 → 未配置'));
		if (r.mail_account !== undefined) {
			var m = r.mail_account || _('默认（mail 段）');
			if (r.mail_account && r.mail_account_effective !== undefined && !r.mail_account_effective)
				m = _('%s（不可用，已回退默认）').format(r.mail_account);
			parts.push(_('发件账号：%s').format(m));
		}
		if (r.notify_enabled === false)
			parts.push(_('推送 → 未启用'));
		else if (r.notify_url)
			parts.push(_('推送 → %s').format(r.notify_url));
		else
			parts.push(_('推送 → 未配置'));
		if (r.notify_account !== undefined) {
			var p = r.notify_account || _('默认（notify 段）');
			if (r.notify_account && r.notify_account_effective !== undefined && !r.notify_account_effective)
				p = _('%s（不可用，已回退默认）').format(r.notify_account);
			parts.push(_('推送账号：%s').format(p));
		}
		if (r.notify_channel_name)
			parts.push(_('通道：%s').format(r.notify_channel_name));
		return parts.join(' ｜ ');
	},

	fillRooms: function() {
		var self = this;
		var d = this.roomsData || {};
		var list = d.rooms || [];
		var unit = unitOf(this.status);
		var nodes = [];

		nodes.push(E('h3', {}, _('已监控的房间（%s 个）').format(d.count || 0)));

		if (!list.length) {
			nodes.push(E('p', {}, E('em', {}, _('还没有配置任何房间。用下面的搜索框找到宿舍后勾选（可多选），再点「添加选中的 N 个」。'))));
			dom.content(this.roomsNode, nodes);
			return;
		}

		var rows = [ E('tr', { 'class': 'tr table-titles' }, [
			E('th', {}, _('房间')),
			E('th', {}, _('编号')),
			E('th', {}, _('余额')),
			E('th', {}, _('档位')),
			E('th', {}, _('阈值')),
			E('th', {}, _('送达')),
			E('th', {}, _('上次查询')),
			E('th', { 'class': 'right' }, _('操作'))
		]) ];

		list.forEach(function(r) {
			var level = r.level || 'unknown';
			rows.push(E('tr', { 'class': 'tr' + (r.enabled === false ? ' powerfee-muted' : '') }, [
				E('td', {}, [
					E('strong', {}, r.display || dash()),
					(!r.label && r.campus) ? '（%s）'.format(r.campus) : '',
					(r.enabled === false) ? ' ' + E('em', {}, _('（已停用）')) : ''
				]),
				E('td', {}, r.num || dash()),
				E('td', {}, (r.balance === null || r.balance === undefined) ? dash() : '%s %s'.format(r.balance, unit)),
				E('td', {}, levelBadge(level)),
				E('td', {}, '%s %s'.format(r.threshold, unit)),
				E('td', { 'style': 'font-size:12px' }, self.deliveryText(r)),
				E('td', { 'style': 'font-size:12px' }, fmtTime(r.last_ok_at)),
				E('td', { 'class': 'right', 'style': 'white-space:nowrap' }, [
					E('button', {
						'class': 'cbi-button cbi-button-action',
						'click': ui.createHandlerFn(self, 'handleEdit', r)
					}, _('编辑')),
					' ',
					E('button', {
						'class': 'cbi-button',
						'click': ui.createHandlerFn(self, 'handleToggle', r)
					}, r.enabled === false ? _('启用') : _('停用')),
					' ',
					E('button', {
						'class': 'cbi-button cbi-button-remove',
						'click': ui.createHandlerFn(self, 'handleRemove', r)
					}, _('删除'))
				])
			]));
		});

		nodes.push(E('div', { 'class': 'powerfee-scroll' }, E('table', { 'class': 'table' }, rows)));
		nodes.push(E('p', { 'class': 'cbi-value-description' },
			_('「停用」只是不再监控这个房间（配置与历史保留）；「删除」会连它的状态与历史一起清掉。' +
			  '每个房间的提醒只发给它自己的收件人、发件账号与推送通道。')));

		dom.content(this.roomsNode, nodes);
	},

	refreshRooms: function() {
		var self = this;
		return pfJson([ 'json', 'room-list' ]).then(function(res) {
			self.roomsData = res;
			self.buildMonitored();
			self.fillRooms();
			self.fillNotice();
			/* 搜索结果里的「已监控」标记也跟着更新（比如刚添加成功的房间） */
			if (self.lastResult)
				self.renderRooms(self.lastResult.list, self.lastResult.title, self.lastResult.note);
			return res;
		}, function() {
			/* 刷新失败不影响刚才的操作提示 */
		});
	},

	refreshStatus: function() {
		var self = this;
		return pfJson([ 'json', 'status' ]).then(function(s) {
			self.status = s;
			self.fillNotice();
		}, function() {
			/* 忽略 */
		});
	},

	handleAdoptCurrent: function(ev) {
		var self = this;
		var num = this.status && this.status.room_num;
		if (!num) {
			ui.addNotification(null, E('p', {}, _('还没有选择宿舍房间。')), 'warning');
			return;
		}
		return withBusy(_('正在把当前房间转成多房间配置…'),
			pfJsonSafe([ 'json', 'room-add', String(num) ])).then(function(res) {
				if (notifyResult(res, _('已加入多房间管理'))) {
					ui.addNotification(null, E('p', {},
						_('原来的状态与历史已继承给这个房间；之后可以继续添加别的房间。')), 'info');
					return Promise.all([ self.refreshRooms(), self.refreshStatus() ]);
				}
			}).catch(notifyError);
	},

	/* ---------- 编辑 / 停用 / 删除 ---------- */

	handleEdit: function() {
		var room = handlerArgs(arguments)[0];
		var self = this;
		var fields = {};

		function input(key, value, placeholder) {
			fields[key] = E('input', {
				'type': 'text',
				'class': 'cbi-input-text',
				'value': (value === null || value === undefined) ? '' : String(value),
				'placeholder': placeholder || ''
			});
			return fields[key];
		}

		var tokenNote = room.notify_token_set
			? _('已保存一个令牌（不会回显）。留空 = 不修改；勾选下面的复选框 = 清除。')
			: _('这个房间还没有自己的令牌（继承全局 notify.token）。');
		fields.clearToken = E('input', { 'type': 'checkbox' });
		fields.clearUrl = E('input', { 'type': 'checkbox' });
		fields.enabled = E('input', { 'type': 'checkbox' });
		if (room.enabled !== false)
			fields.enabled.checked = true;

		var notifyEnabledSel = E('select', { 'class': 'cbi-input-select' }, [
			E('option', { 'value': '', 'selected': (room.notify_enabled === undefined || room.notify_enabled === null) ? '' : null }, _('继承全局开关')),
			E('option', { 'value': '1', 'selected': room.notify_enabled === true ? '' : null }, _('启用')),
			E('option', { 'value': '0', 'selected': room.notify_enabled === false ? '' : null }, _('停用'))
		]);
		fields.notifyEnabled = notifyEnabledSel;

		/* 发件账号 / 推送账号下拉（1.2.0）：留空 = 跟随默认（mail / notify 段）。
		   账号清单读不到（旧版本）时只有「跟随默认」，房间原有的引用会保留成一项，不会被静默清掉。 */
		function accountSelect(list, current, emptyLabel) {
			var sel = E('select', { 'class': 'cbi-input-select' });
			sel.appendChild(E('option', { 'value': '', 'selected': (current ? null : '') }, emptyLabel));
			var found = false;
			(list || []).forEach(function(acc) {
				if (acc.name === current)
					found = true;
				var label = acc.name;
				if (!acc.enabled)
					label = _('%s（已停用）').format(acc.name);
				else if (acc.usable === false)
					label = _('%s（不可用）').format(acc.name);
				sel.appendChild(E('option', {
					'value': acc.name,
					'selected': (acc.name === current) ? '' : null
				}, label));
			});
			if (!found && current)
				sel.appendChild(E('option', { 'value': current, 'selected': '' },
					_('%s（当前引用，已找不到）').format(current)));
			return sel;
		}

		var curMailAccount = String(room.mail_account || '');
		var curNotifyAccount = String(room.notify_account || '');
		fields.mailAccount = accountSelect(this.mailAccounts, curMailAccount, _('跟随默认（mail 段）'));
		fields.notifyAccount = accountSelect(this.pushAccounts, curNotifyAccount, _('跟随默认（notify 段）'));

		var acctNote = (this.mailAccounts === null || this.pushAccounts === null)
			? E('p', { 'class': 'cbi-value-description' },
				_('读不到账号列表（powerfee 可能还是旧版本，没有 mail-account / push-account 子命令）：' +
				  '这两个下拉暂时只有「跟随默认」可选，升级到 1.2.0 后即可按房间指定账号。'))
			: '';

		var body = E('div', { 'class': 'cbi-map' }, [
			row(_('监控'), E('label', {}, [ fields.enabled, ' ', _('启用这个房间的监控') ])),
			row(_('房间编号'), input('num', room.num, _('接口里的房间编号，如 1001'))),
			row(_('展示名'), input('label', room.label, _('留空则用「楼栋 房间」，如 1号楼 A101'))),
			row(_('告警阈值'), input('threshold', room.threshold, _('留空继承 main.threshold（当前 %s）').format(this.status.threshold))),
			row(_('收件邮箱'), input('mail_to', room.mail_to, _('留空继承 mail.to；多个用逗号分隔'))),
			row(_('发件账号'), fields.mailAccount),
			row(_('推送地址'), input('notify_url', '', room.notify_url
				? _('已配置：%s（留空 = 不修改）').format(room.notify_url)
				: _('留空继承 notify.url'))),
			row('', E('label', {}, [ fields.clearUrl, ' ', _('清除本房间的推送地址（改回继承 notify.url）') ])),
			row(_('推送令牌'), input('notify_token', '', _('留空 = 不修改'))),
			row('', E('label', {}, [ fields.clearToken, ' ', _('清除本房间的推送令牌（改回继承 notify.token）') ])),
			row(_('推送账号'), fields.notifyAccount),
			row(_('推送开关'), notifyEnabledSel),
			E('p', { 'class': 'cbi-value-description' }, tokenNote),
			acctNote,
			E('p', { 'class': 'cbi-value-description' },
				_('提示：收件人、发件账号与推送通道留空都表示「继承设置页里的默认值」（mail / notify 段），' +
				  '只有需要单独发给别人、或用不同发件邮箱时才填。'))
		]);

		ui.showModal(_('编辑房间：%s').format(room.display || room.num), [
			body,
			E('div', { 'class': 'right' }, [
				E('button', {
					'class': 'btn cbi-button cbi-button-neutral',
					'click': ui.hideModal
				}, _('取消')),
				' ',
				E('button', {
					'class': 'btn cbi-button cbi-button-save important',
					'click': ui.createHandlerFn(self, 'handleSaveEdit', room, fields)
				}, _('保存'))
			])
		]);
	},

	handleSaveEdit: function() {
		var _a = handlerArgs(arguments), room = _a[0], fields = _a[1];
		var self = this;
		var id = room.id || room.section;
		var kvs = [
			'enabled=' + (fields.enabled.checked ? '1' : '0'),
			'num=' + String(fields.num.value || '').trim(),
			'label=' + String(fields.label.value || '').trim(),
			'threshold=' + String(fields.threshold.value || '').trim(),
			'mail_to=' + String(fields.mail_to.value || '').trim(),
			'notify_enabled=' + String(fields.notifyEnabled.value || '')
		];
		var url = String(fields.notify_url.value || '').trim();
		if (url)
			kvs.push('notify_url=' + url);
		else if (fields.clearUrl.checked)
			kvs.push('notify_url=');
		var token = String(fields.notify_token.value || '').trim();
		if (token)
			kvs.push('notify_token=' + token);
		else if (fields.clearToken.checked)
			kvs.push('notify_token=');

		/* 账号引用（1.2.0）：空值 = 跟随默认。旧版引擎不认识这两个键（会报「不支持的键」），
		   所以读不到账号清单、且房间本来也没引用时就不发它们。 */
		var mailAcct = String(fields.mailAccount.value || '').trim();
		if (this.mailAccounts !== null || mailAcct)
			kvs.push('mail_account=' + mailAcct);
		var notifyAcct = String(fields.notifyAccount.value || '').trim();
		if (this.pushAccounts !== null || notifyAcct)
			kvs.push('notify_account=' + notifyAcct);

		ui.hideModal();
		return withBusy(_('正在保存…'), pfJsonSafe([ 'json', 'room-save', id ].concat(kvs))).then(function(res) {
			if (notifyResult(res, _('已保存'))) {
				return Promise.all([ self.refreshRooms(), self.refreshStatus() ]);
			}
		}).catch(notifyError);
	},

	handleToggle: function() {
		var room = handlerArgs(arguments)[0];
		var self = this;
		var id = room.id || room.section;
		var want = (room.enabled === false) ? '1' : '0';
		return withBusy(_('正在保存…'), pfJsonSafe([ 'json', 'room-save', id, 'enabled=' + want ]))
			.then(function(res) {
				if (notifyResult(res, want === '1' ? _('已启用') : _('已停用')))
					return self.refreshRooms();
			}).catch(notifyError);
	},

	handleRemove: function() {
		var room = handlerArgs(arguments)[0];
		var self = this;
		var id = room.id || room.section;
		ui.showModal(_('删除房间'), [
			E('p', {}, _('确定要删除房间「%s」吗？它的余额状态与历史采样会一起清除。').format(room.display || room.num)),
			E('div', { 'class': 'right' }, [
				E('button', { 'class': 'btn cbi-button cbi-button-neutral', 'click': ui.hideModal }, _('取消')),
				' ',
				E('button', {
					'class': 'btn cbi-button cbi-button-remove',
					'click': function(ev) {
						ui.hideModal();
						withBusy(_('正在删除…'), pfJsonSafe([ 'json', 'room-remove', id ])).then(function(res) {
							if (notifyResult(res, _('已删除')))
								return Promise.all([ self.refreshRooms(), self.refreshStatus() ]);
						}).catch(notifyError);
					}
				}, _('删除'))
			])
		]);
	},

	/* ---------- 级联下拉（数据来自 json groups） ---------- */

	fillCampuses: function() {
		var opts = [ E('option', { 'value': '' }, _('— 请选择 —')) ];

		(this.groups || []).forEach(function(g) {
			opts.push(E('option', { 'value': g.campus }, g.campus));
		});

		dom.content(this.campusSel, opts);
		this.fillBuildings('');
	},

	fillBuildings: function(campus) {
		var opts = [ E('option', { 'value': '' }, _('— 请选择 —')) ];

		(this.groups || []).forEach(function(g) {
			if (g.campus !== campus)
				return;
			(g.buildings || []).forEach(function(b) {
				opts.push(E('option', { 'value': b }, b));
			});
		});

		dom.content(this.buildingSel, opts);
	},

	/* ---------- 房间列表（搜索结果，可多选） ---------- */

	/* 已监控的房间：编号 -> 段名（供「已监控」标记与复选框禁用） */
	buildMonitored: function() {
		var self = this;
		this.monitored = {};
		this.monitoredKeys = {};   /* 校区|楼栋|房间 -> 段名（接口没有编号时的兜底） */
		((this.roomsData || {}).rooms || []).forEach(function(r) {
			var num = (r.num === null || r.num === undefined) ? '' : String(r.num);
			var sec = r.id || r.section || '';
			if (num)
				self.monitored[num] = sec;
			var key = [ r.campus || '', r.building || '', r.room || '' ].join('|');
			if (key !== '||')
				self.monitoredKeys[key] = sec;
		});
	},

	/* 这个搜索结果已经在监控里吗？返回段名（不在监控里返回空） */
	isMonitored: function(r) {
		var num = roomId(r);
		if (num && this.monitored && this.monitored[num])
			return this.monitored[num];
		var key = [ r.campus || '', r.building || '', r.room || '' ].join('|');
		if (key !== '||' && this.monitoredKeys && this.monitoredKeys[key])
			return this.monitoredKeys[key];
		return '';
	},

	/* 当前显示的行里可选（未监控）的数量与已选数量 */
	countPage: function() {
		var self = this, total = 0, sel = 0;
		(this.resultRows || []).forEach(function(rec) {
			if (rec.sec)
				return;
			total++;
			if (self.selected[rec.key])
				sel++;
		});
		return { total: total, selected: sel };
	},

	/* 表头复选框：全选/全不选本页（只作用于当前显示的行） */
	applyHeadCb: function() {
		var c = this.countPage();
		this.pageSelectable = c.total;
		if (this.headCb) {
			this.headCb.checked = (c.total > 0 && c.selected === c.total);
			this.headCb.disabled = (c.total === 0);
		}
	},

	renderRooms: function(list, title, note) {
		var self = this;

		this.lastResult = { list: list, title: title, note: note };
		this.resultRows = [];
		this.rowMap = {};

		var headCb = E('input', { 'type': 'checkbox' });
		headCb.addEventListener('click', function(ev) {
			ev.stopPropagation();
			self.setAllOnPage(ev.target.checked);
		});
		this.headCb = headCb;

		var rows = [ E('tr', { 'class': 'tr table-titles' }, [
			E('th', { 'style': 'width:2em' }, headCb),
			E('th', {}, _('校区')),
			E('th', {}, _('楼栋')),
			E('th', {}, _('房间')),
			E('th', { 'class': 'right' }, _('余额')),
			E('th', { 'class': 'right' }, _('选择'))
		]) ];

		list.forEach(function(r) {
			var key = selKey(r);
			var sec = self.isMonitored(r);
			var picked = !sec && !!self.selected[key];
			var cb = E('input', { 'type': 'checkbox' });
			if (picked)
				cb.checked = true;
			if (sec)
				cb.disabled = true;
			var btn = E('button', {
				'class': 'cbi-button' + (sec ? '' : (picked ? ' cbi-button-neutral' : ' cbi-button-action')),
				'disabled': sec ? true : null,
				'click': function(ev) {
					ev.stopPropagation();
					self.setPicked(key, !self.selected[key]);
				}
			}, sec ? _('已监控') : (picked ? _('取消选中') : _('选中')));
			var tr = E('tr', {
				'class': 'tr' + (picked ? ' powerfee-picked' : ''),
				'data-room': key,
				'click': function(ev) {
					if (sec)
						return;
					self.setPicked(key, !self.selected[key]);
				}
			}, [
				E('td', {}, cb),
				E('td', {}, r.campus || ''),
				E('td', {}, r.building || ''),
				E('td', {}, [ E('strong', {}, r.room), sec ? monitoredBadge(sec) : '' ]),
				E('td', { 'class': 'right' }, r.balance),
				E('td', { 'class': 'right' }, btn)
			]);
			var rec = { tr: tr, cb: cb, btn: btn, room: r, sec: sec, key: key };
			if (!self.rowMap[key])
				self.rowMap[key] = [];
			self.rowMap[key].push(rec);
			self.resultRows.push(rec);
			rows.push(tr);
		});

		this.applyHeadCb();

		dom.content(this.resultNode, [
			E('h3', {}, title || _('房间列表')),
			note ? E('p', { 'class': 'cbi-value-description' }, note) : '',
			list.length
				? E('div', { 'class': 'powerfee-scroll' }, E('table', { 'class': 'table' }, rows))
				: E('p', {}, E('em', {}, _('没有匹配的房间。换个关键词试试，比如只输房间号的一部分。'))),
			list.length
				? E('p', { 'class': 'cbi-value-description' },
					_('勾选（或点整行）可以选择要监控的房间，一次可以选多个；标着「已监控」的房间已经在监控里，会被自动跳过。'))
				: ''
		]);

		this.fillPick();
	},

	/* 同步一行（或多行同键）的勾选外观；不刷新面板（批量勾选时由调用方收尾） */
	applyPicked: function(key, on) {
		((this.rowMap || {})[key] || []).forEach(function(rec) {
			rec.cb.checked = !!on;
			if (on)
				rec.tr.classList.add('powerfee-picked');
			else
				rec.tr.classList.remove('powerfee-picked');
			if (rec.btn && !rec.sec) {
				rec.btn.textContent = on ? _('取消选中') : _('选中');
				rec.btn.className = 'cbi-button' + (on ? ' cbi-button-neutral' : ' cbi-button-action');
			}
		});
	},

	/* 勾选/取消一个房间（已监控的行不动） */
	setPicked: function(key, on) {
		var recs = (this.rowMap || {})[key] || [];
		var blocked = false;
		recs.forEach(function(rec) {
			if (rec.sec)
				blocked = true;
		});
		if (!key || blocked)
			return;
		if (on)
			this.selected[key] = this.selected[key] || (recs[0] ? recs[0].room : { room: key });
		else
			delete this.selected[key];
		this.applyPicked(key, on);
		this.applyHeadCb();
		this.fillPick();
	},

	/* 全选/全不选当前显示的行（已监控的自动跳过） */
	setAllOnPage: function(on) {
		var self = this;
		(this.resultRows || []).forEach(function(rec) {
			if (rec.sec)
				return;
			if (on)
				self.selected[rec.key] = self.selected[rec.key] || rec.room;
			else
				delete self.selected[rec.key];
			self.applyPicked(rec.key, on);
		});
		this.applyHeadCb();
		this.fillPick();
	},

	clearSelection: function() {
		var self = this;
		this.selected = {};
		Object.keys(this.rowMap || {}).forEach(function(key) {
			self.applyPicked(key, false);
		});
		this.applyHeadCb();
		this.fillPick();
	},

	handleSearch: function(ev) {
		var self = this;
		var kw = sanitizeKeyword(this.searchInput.value);

		if (!kw) {
			ui.addNotification(null, E('p', {}, _('请输入房间号或其中一部分。')), 'warning');
			return;
		}

		return withBusy(_('正在搜索「%s」…').format(kw), pfJsonSafe([ 'json', 'rooms', kw, SEARCH_LIMIT ])).then(function(res) {
			var err = apiError(res);
			if (err) {
				self.renderError(_('搜索失败'), err);
				return;
			}

			var list = res.rooms || [];
			var note = _('关键词「%s」匹配 %s 间，显示前 %s 间。').format(kw, res.total, res.shown);
			if (res.truncated)
				note += _('结果被截断，请用更完整的房间号缩小范围。');
			self.renderRooms(list, _('搜索结果'), note);
		}).catch(notifyError);
	},

	handleBrowse: function(ev) {
		var campus = this.campusSel.value;
		var building = this.buildingSel.value;
		var self = this;

		if (!campus || !building) {
			ui.addNotification(null, E('p', {}, _('请先选择校区和楼栋。')), 'warning');
			return;
		}

		/* 已经加载过全部房间：直接本地筛选，不再请求接口 */
		if (this.allRooms) {
			this.showBuildingFromLocal(campus, building);
			return;
		}

		/* 先用楼栋名做关键词（接口按房间号/楼栋/校区过滤）；若接口只认房间号（旧版），
		   结果为空时再退回用楼栋名里的数字重试一次 */
		var digit = (String(building).match(/\d+/) || [ null ])[0];

		return withBusy(_('正在获取「%s」的房间…').format(building),
			pfJsonSafe([ 'json', 'rooms', building, BROWSE_LIMIT ])).then(function(res) {
				var err = apiError(res);
				if (err) {
					self.renderError(_('获取房间列表失败'), err);
					return;
				}

				var all = res.rooms || [];
				var list = all.filter(function(r) {
					return r.campus === campus && r.building === building;
				});

				if (!list.length && digit) {
					return pfJsonSafe([ 'json', 'rooms', digit, BROWSE_LIMIT ]).then(function(res2) {
						var err2 = apiError(res2);
						if (err2) {
							self.renderError(_('获取房间列表失败'), err2);
							return;
						}
						var all2 = res2.rooms || [];
						var list2 = all2.filter(function(r) {
							return r.campus === campus && r.building === building;
						});
						var note2 = _('服务端用关键词「%s」取回 %s 间，本地筛出「%s」%s 间。')
							.format(digit, all2.length, building, list2.length);
						if (res2.truncated)
							note2 += _('服务端结果被截断，可能不完整，建议用房间号搜索或「加载全部房间」。');
						self.renderRooms(list2, _('「%s」的房间').format(building), note2);
					});
				}

				var note = _('服务端用关键词「%s」取回 %s 间，本地筛出「%s」%s 间。')
					.format(building, all.length, building, list.length);
				if (res.truncated)
					note += _('服务端结果被截断，可能不完整，建议用房间号搜索或「加载全部房间」。');
				self.renderRooms(list, _('「%s」的房间').format(building), note);
			}).catch(notifyError);
	},

	showBuildingFromLocal: function(campus, building) {
		var list = (this.allRooms || []).filter(function(r) {
			return r.campus === campus && r.building === building;
		});
		this.renderRooms(list, _('「%s」的房间').format(building),
			_('来自已加载的全部房间（本地筛选），共 %s 间。').format(list.length));
	},

	handleLoadAll: function(ev) {
		var self = this;

		if (this.allRooms) {
			ui.addNotification(null, E('p', {}, _('已经加载过全部房间了，直接选校区/楼栋浏览即可。')), 'info');
			return;
		}

		return withBusy(_('正在加载全部房间…'),
			pfJsonSafe([ 'json', 'rooms', '', ALL_LIMIT ])).then(function(res) {
				var err = apiError(res);
				if (err) {
					self.renderError(_('加载失败'), err);
					return;
				}

				self.allRooms = res.rooms || [];
				ui.addNotification(null, E('p', {},
					_('已加载 %s 间房间（接口共返回 %s 间）。选择校区与楼栋后点「浏览该楼房间」即在本地筛选。')
						.format(self.allRooms.length, res.total)), 'success');

				var campus = self.campusSel.value;
				var building = self.buildingSel.value;
				if (campus && building)
					self.showBuildingFromLocal(campus, building);
			}).catch(notifyError);
	},

	/* ---------- 保存（批量添加选中的房间） ---------- */

	fillPick: function() {
		var self = this;
		var keys = Object.keys(this.selected || {});
		var n = keys.length;
		var nodes = [ E('h3', {}, _('添加为监控房间')) ];

		if (n) {
			var names = keys.map(function(k) {
				var r = self.selected[k] || {};
				var label = [ r.building, r.room ].filter(function(x) {
					return x;
				}).join(' ');
				return label || k;
			});
			nodes.push(E('p', {}, [
				_('已选 %s 个房间：').format(n),
				E('strong', {}, names.slice(0, 3).join(_('、'))),
				n > 3 ? _(' 等 %s 个').format(n) : '',
				' ',
				E('button', {
					'class': 'cbi-button',
					'click': function(ev) {
						ev.preventDefault();
						self.clearSelection();
					}
				}, _('清空选择'))
			]));
		}
		else {
			nodes.push(E('p', {}, E('em', {},
				_('还没有选择房间。在上面的搜索结果里勾选（或点整行）—— 一次可以选多个。'))));
		}

		var c = this.countPage();
		if (c.total > 0) {
			var allSel = (c.selected === c.total);
			nodes.push(E('button', {
				'class': 'cbi-button cbi-button-neutral',
				'click': function(ev) {
					ev.preventDefault();
					self.setAllOnPage(!allSel);
				}
			}, allSel ? _('取消全选（本页 %s 个）').format(c.total)
				: _('全选本页 %s 个').format(c.total)));
			nodes.push(' ');
		}

		nodes.push(E('button', {
			'class': 'cbi-button cbi-button-save important',
			'disabled': n ? null : true,
			'click': ui.createHandlerFn(this, 'handleAddRoom')
		}, n ? _('添加选中的 %s 个').format(n) : _('添加')));
		nodes.push(E('p', { 'class': 'cbi-value-description' },
			_('添加后立即由服务按间隔自动查询；第一次查询就会把当前余额写进它的状态与历史。' +
			  '收件人与推送通道默认继承「设置」页里的全局值，可在上面的列表里单独修改。' +
			  '已经在监控里的房间会被自动跳过。')));

		dom.content(this.pickNode, nodes);
	},

	handleAddRoom: function(ev) {
		var self = this;
		var keys = Object.keys(this.selected || {});

		if (!keys.length) {
			ui.addNotification(null, E('p', {}, _('请先在上面的列表里勾选要监控的房间。')), 'warning');
			return;
		}

		var picked = this.selected;

		/* 一次调用带上所有选中编号：主程序只请求接口一次，逐房间返回结果 */
		return withBusy(_('正在添加 %s 个房间…').format(keys.length),
			pfJsonAny([ 'json', 'room-add', '--batch' ].concat(keys))).then(function(res) {
				self.reportAddResults(res, picked);
				return Promise.all([ self.refreshRooms(), self.refreshStatus() ]);
			}).catch(notifyError);
	},

	/* 逐房间报告批量添加的结果：成功 / 已在监控（跳过） / 失败 分别提示 */
	reportAddResults: function(res, picked) {
		var results = (res && Array.isArray(res.results)) ? res.results : null;

		function label(r) {
			var room = (picked || {})[r.target];
			var name = room ? [ room.building, room.room ].filter(function(x) {
				return x;
			}).join(' ') : '';
			var num = r.num || r.target || '';
			if (name)
				return _('%s（编号 %s）').format(name, num);
			return _('编号 %s').format(num);
		}

		/* 旧版主程序没有 results[]，或整批在拉取阶段就失败：退回单条提示 */
		if (!results || !results.length) {
			notifyResult(res, _('已添加'));
			this.clearSelection();
			return;
		}

		var okList = [], skipList = [], failList = [];
		results.forEach(function(r) {
			if (r.skipped)
				skipList.push(r);
			else if (r.ok)
				okList.push(r);
			else
				failList.push(r);
		});

		function pre(list) {
			return E('pre', { 'style': 'white-space:pre-wrap;margin:0' }, list.join('\n'));
		}

		if (okList.length) {
			ui.addNotification(null, E('div', {}, [
				E('h4', {}, _('已添加 %s 个房间').format(okList.length)),
				pre(okList.map(function(r) {
					return label(r) + (r.id ? _('（config room %s）').format(r.id) : '');
				}))
			]), 'success');
		}
		if (skipList.length) {
			ui.addNotification(null, E('div', {}, [
				E('h4', {}, _('已在监控，已跳过 %s 个').format(skipList.length)),
				pre(skipList.map(function(r) {
					return label(r) + '：' + (r.message || _('已在监控'));
				}))
			]), 'warning');
		}
		if (failList.length) {
			ui.addNotification(null, E('div', {}, [
				E('h4', {}, _('添加失败 %s 个').format(failList.length)),
				pre(failList.map(function(r) {
					return label(r) + '：' + (r.message || _('未知原因'));
				}))
			]), 'danger');
		}

		this.clearSelection();
	}
});
