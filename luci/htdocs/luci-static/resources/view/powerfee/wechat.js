'use strict';
'require view';
'require fs';
'require ui';
'require dom';

/*
 * 宿舍电量哨兵 · 微信推送页
 *
 * 页面分四段：
 *   ① 状态卡：当前通道 / 开关 / 推送地址（掩码）/ 密钥提示（掩码）/ 最近一次推送结果
 *              + 「发测试消息」「刷新」
 *   ② 推送账号：命名推送账号（config push_account）的列表 —— 名称 / 通道 / 掩码地址 / 状态
 *              + 新增 / 编辑 / 删除 / 发测试消息（给不同房间指定不同推送目标用）
 *   ③ 通道选择器：Server酱 / 企业微信群机器人 / ClawBot / 自定义 webhook / 关闭
 *   ④ 所选通道的表单：填好参数点「启用并测试」—— 后端会写 UCI → commit → 立刻发一条
 *      测试消息，并把结果（含服务端原文）带回来。ClawBot 那一格是原来的五步向导。
 *
 * 取数路线：**走命令行** `powerfee push <...>`（通道管理）、`powerfee wechat <...>`
 * （ClawBot 向导）与 `powerfee push-account <...>` / `powerfee json push-accounts`
 * （命名推送账号），不碰 /cgi-bin/powerfee 的查询令牌，理由同 1.0.2：
 *   * 查询端点默认是关的（notify.http_enabled=0），走 CGI 会先卡在"请先开启端点"；
 *   * 端点令牌是给外部机器人用的密钥，没必要让浏览器读一遍 UCI 再带进 URL；
 *   * 现有三个页面本来就走 fs.exec('/usr/bin/powerfee', ...)，
 *     这条路的 ACL 已经授好，本页只额外申请了 /etc/init.d/weclawbot-api（启动/重启服务）。
 *
 * 密钥永远不回显：`push status` 只回掩码（url_masked / key_hint）、
 * `json push-accounts` 也只回掩码与「是否已设置」，页面把它们当「已保存」提示；
 * 要换密钥就在输入框里填新的（输入框只用于新值，不会把旧值填回去）。
 */

var PF_BIN = '/usr/bin/powerfee';
var WECHAT_INIT = '/etc/init.d/weclawbot-api';

var WAIT_POLL = 5;      /* 等扫码 / 等激活时的轮询间隔（秒） */
var IDLE_POLL = 20;     /* 其它状态的轮询间隔（秒） */
var QR_TTL = 90;        /* 二维码自动刷新间隔（秒，服务端约 2 分钟换一张） */

var COLOR = { ok: '#188038', warn: '#e8710a', err: '#d93025', dim: '#5f6368', link: '#1a73e8' };

var STYLE = '' +
	'.pfw-steps{margin:0 0 1em 0;padding:0;list-style:none}' +
	'.pfw-steps li{padding:4px 0 4px 4px;color:%s;font-size:13px}'.format(COLOR.dim) +
	'.pfw-steps li.done{color:%s}'.format(COLOR.ok) +
	'.pfw-steps li.current{color:#202124;font-weight:700}' +
	'.pfw-step{margin-top:.5em}' +
	'.pfw-why{color:%s;font-size:12px;margin:.2em 0 .8em 0;line-height:1.6}'.format(COLOR.dim) +
	'.pfw-cmd{background:#f8f9fa;border:1px solid #dadce0;border-radius:4px;padding:8px 10px;' +
	'margin:6px 0;font-family:monospace;font-size:12px;white-space:pre-wrap;word-break:break-all}' +
	'.pfw-qr{background:#fff;border:1px solid #dadce0;border-radius:6px;padding:10px;display:inline-block}' +
	'.pfw-qr img{width:300px;height:300px;image-rendering:pixelated;display:block}' +
	'.pfw-badge{display:inline-block;padding:2px 10px;border-radius:10px;color:#fff;font-size:12px}' +
	'.pfw-note{color:%s;font-size:12px;line-height:1.6}'.format(COLOR.dim) +
	'.pfw-table td{padding:3px 12px 3px 0;vertical-align:top;font-size:13px}' +
	'.pfw-table td.k{color:%s;white-space:nowrap}'.format(COLOR.dim) +
	'.pfw-chans{display:flex;flex-wrap:wrap;gap:6px;margin:.4em 0}' +
	'.pfw-chan{border:1px solid #dadce0;background:#fff;border-radius:6px;padding:6px 12px;' +
	'cursor:pointer;font-size:13px;color:#3c4043}' +
	'.pfw-chan:hover{border-color:#1a73e8}' +
	'.pfw-chan.active{border-color:#1a73e8;background:#e8f0fe;color:#174ea6;font-weight:700}' +
	'.pfw-field{margin:.7em 0}' +
	'.pfw-field label{display:block;font-size:12px;color:%s;margin-bottom:3px}'.format(COLOR.dim) +
	'.pfw-field input[type=text],.pfw-field input[type=password]{width:100%;max-width:420px;' +
	'box-sizing:border-box;padding:5px 8px}' +
	'.pfw-field textarea{width:100%;max-width:520px;box-sizing:border-box;padding:5px 8px;' +
	'font-family:monospace;font-size:12px}' +
	'.pfw-res{border-radius:6px;padding:8px 10px;margin:.7em 0;font-size:13px;' +
	'white-space:pre-wrap;word-break:break-all}' +
	'.pfw-res.ok{background:#e6f4ea;border:1px solid #ceead6;color:#137333}' +
	'.pfw-res.err{background:#fce8e6;border:1px solid #f6aea9;color:#c5221f}' +
	'.pfw-actions{margin-top:.8em}';

/* 推送通道清单：key 与 push.py 的 channel 字段一致 */
var CHANNELS = [
	{
		key: 'serverchan',
		title: _('Server酱'),
		desc: _('最省事：不用在路由器上装任何服务，填一个 SendKey 就能把告警推到微信。')
	},
	{
		key: 'wecom',
		title: _('企业微信群机器人'),
		desc: _('在企业的微信群里加一个群机器人，把 Webhook 地址粘进来即可。')
	},
	{
		key: 'clawbot',
		title: _('ClawBot'),
		desc: _('在路由器上常驻一个微信机器人服务，直接推到你的微信会话（要装服务、扫码）。')
	},
	{
		key: 'webhook',
		title: _('自定义 webhook'),
		desc: _('任何能收 HTTP 请求的地址都行：Bark、ntfy、钉钉、自建脚本……')
	},
	{
		key: 'off',
		title: _('关闭推送'),
		desc: _('不发推送（邮件提醒不受影响）。')
	}
];

/* 命名推送账号（config push_account）的 channel 取值 —— 与引擎契约一致；
   留空 = 继承 notify 段。显示时兼容旧叫法 webhook。 */
var ACCOUNT_CHANNELS = [
	{ value: '', title: _('跟随默认（notify 段）') },
	{ value: 'serverchan', title: _('Server酱') },
	{ value: 'wecom', title: _('企业微信群机器人') },
	{ value: 'clawbot', title: _('ClawBot') },
	{ value: 'custom', title: _('自定义 webhook') }
];

function accountChannelTitle(v) {
	v = String(v || '');
	if (v === 'webhook')                      /* 旧叫法，等同 custom */
		v = 'custom';
	var hit = ACCOUNT_CHANNELS.filter(function(c) { return c.value === v; })[0];
	return hit ? hit.title : (v || _('跟随默认（notify 段）'));
}

/* ---------- 账号接口小工具（接口没落地 / 旧版本时只提示，不崩） ---------- */

/* 跑一条 powerfee 命令：只看退出码；stdout 是 JSON 就解析，否则包一层 */
function pfRun(args) {
	return fs.exec(PF_BIN, args).then(function(res) {
		var out = (res.stdout || '').trim();
		var err = (res.stderr || '').trim();
		if (res.code !== 0)
			throw new Error(err || out || _('powerfee 执行失败（退出码 %s）').format(res.code));
		if (!out)
			return { ok: true };
		try {
			return JSON.parse(out);
		}
		catch (e) {
			return { ok: true, message: out };
		}
	});
}

/* json push-accounts 的形状：accounts / list / 裸数组都认 */
function accountList(res) {
	if (!res || res.error)
		return null;
	var list = res.accounts || res.list || null;
	if (!list && Array.isArray(res))
		list = res;
	return Array.isArray(list) ? list : null;
}

function accName(a) {
	return String((a && (a.name || a.section || a.id)) || '').trim();
}

function accDisabled(a) {
	return !!(a && (a.enabled === false || a.enabled === '0' || a.enabled === 0));
}

/* 取第一个「有值」的字段（'' / null / undefined 视为没有） */
function firstVal(a, keys) {
	for (var i = 0; i < keys.length; i++) {
		var v = a ? a[keys[i]] : undefined;
		if (v !== undefined && v !== null && v !== '')
			return v;
	}
	return '';
}

/* 取第一个「是布尔」的字段（「是否可用」这类标志） */
function firstBool(a, keys) {
	for (var i = 0; i < keys.length; i++) {
		var v = a ? a[keys[i]] : undefined;
		if (typeof v === 'boolean')
			return v;
		if (v === '1' || v === 1 || v === 'true')
			return true;
		if (v === '0' || v === 0 || v === 'false')
			return false;
	}
	return null;
}

/* 账号对象里的字段可能平铺，也可能放在 raw / effective 子对象里
   （powerfee json push-accounts 的形状：raw = 段里实际写的值，空 = 继承；
   effective = 继承解析后的有效值）—— 两层都找，哪层先有值用哪层。 */
function acctVal(a, keys, sub) {
	var v = firstVal(a, keys);
	if (v !== '')
		return v;
	if (sub)
		return firstVal(a ? a[sub] : null, keys);
	return '';
}

function acctRaw(a, keys) { return acctVal(a, keys, 'raw'); }
function acctEff(a, keys) { return acctVal(a, keys, 'effective'); }

function acctBool(a, keys) {
	var v = firstBool(a, keys);
	if (v !== null)
		return v;
	v = firstBool(a ? a.raw : null, keys);
	if (v !== null)
		return v;
	return firstBool(a ? a.effective : null, keys);
}

/* 账号是否可用：优先用引擎回的布尔字段，没有就按「有没有地址」推断 */
function accountUsable(a) {
	var flag = acctBool(a, [ 'usable', 'ok', 'ready', 'valid', 'configured' ]);
	if (flag !== null)
		return flag;
	var ch = String(acctRaw(a, [ 'channel' ]) || acctEff(a, [ 'channel' ]) || '');
	var url = acctEff(a, [ 'url_masked' ]);
	return !!(url || ch === 'clawbot');
}

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

function badge(text, color) {
	return E('span', { 'class': 'pfw-badge', 'style': 'background:%s'.format(color || COLOR.dim) }, text);
}

function row(k, v) {
	return E('tr', {}, [
		E('td', { 'class': 'k' }, k),
		E('td', {}, v)
	]);
}

function copyButton(text, label) {
	return E('button', {
		'class': 'cbi-button cbi-button-neutral',
		'click': function(ev) {
			ev.preventDefault();
			var done = function() {
				ui.addNotification(null, E('p', {}, _('已复制到剪贴板。')), 'info');
			};
			var fallback = function() {
				var ta = E('textarea', { 'style': 'position:fixed;left:-9999px;top:0' }, text);
				document.body.appendChild(ta);
				ta.select();
				var ok = false;
				try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
				document.body.removeChild(ta);
				if (ok)
					done();
				else
					ui.addNotification(null, E('p', {}, _('浏览器不让自动复制，请手动选中上面的命令复制。')), 'warning');
			};
			if (navigator.clipboard && navigator.clipboard.writeText)
				navigator.clipboard.writeText(text).then(done, fallback);
			else
				fallback();
		}
	}, label || _('复制'));
}

function cmdBlock(text) {
	return E('div', {}, [
		E('pre', { 'class': 'pfw-cmd' }, text),
		copyButton(text)
	]);
}

function notifyError(err) {
	ui.addNotification(null, E('p', {}, _('操作失败：%s').format(err && err.message ? err.message : String(err))), 'danger');
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

/* 带「显示/隐藏」的输入框（密钥默认打码，点一下才能看见自己粘了什么） */
function field(label, id, opts) {
	opts = opts || {};
	var input = E('input', {
		'id': id,
		'type': opts.secret ? 'password' : 'text',
		'class': 'cbi-input-text',
		'placeholder': opts.placeholder || '',
		'spellcheck': 'false',
		'autocomplete': 'off'
	});
	if (opts.value != null)
		input.value = opts.value;

	var line = [ input ];
	if (opts.secret) {
		var btn = E('button', {
			'class': 'cbi-button',
			'style': 'margin-left:6px',
			'click': function(ev) {
				ev.preventDefault();
				var showing = (input.type === 'text');
				input.type = showing ? 'password' : 'text';
				btn.textContent = showing ? _('显示') : _('隐藏');
			}
		}, _('显示'));
		line.push(btn);
	}
	return E('div', { 'class': 'pfw-field' }, [
		E('label', { 'for': id }, label),
		E('div', {}, line),
		opts.hint ? E('div', { 'class': 'pfw-note' }, opts.hint) : ''
	]);
}

function selectField(label, id, options, value) {
	var sel = E('select', { 'id': id, 'class': 'cbi-input-select' });
	options.forEach(function(o) {
		sel.appendChild(E('option', { 'value': o }, o));
	});
	sel.value = value;
	return E('div', { 'class': 'pfw-field' }, [
		E('label', { 'for': id }, label),
		E('div', {}, sel)
	]);
}

function textareaField(label, id, value, hint) {
	return E('div', { 'class': 'pfw-field' }, [
		E('label', { 'for': id }, label),
		E('textarea', { 'id': id, 'class': 'cbi-input-textarea', 'rows': 3, 'spellcheck': 'false' }, value),
		hint ? E('div', { 'class': 'pfw-note' }, hint) : ''
	]);
}

/* ---------- ClawBot 五步状态机 ---------- */

function stepOf(s) {
	if (!s || !s.installed) return 1;                                  /* 服务没装 */
	if (!s.running) return 2;                                          /* 装了没跑 */
	if (!s.bound) return 3;                                            /* 要扫码 */
	if (!s.context_ready) return 4;                                    /* 要激活 */
	if (!(s.notify_enabled && s.notify_url_ok && s.notify_token_set)) return 5;
	return 6;                                                          /* 全部就绪 */
}

var STEP_TITLE = [
	'',
	_('安装微信服务'),
	_('启动服务并扫码登录'),
	_('在微信里发一条消息（激活推送）'),
	_('开启推送'),
	_('完成')
];

var STEP_DONE = [
	'',
	function(s) { return _('服务已安装（%s）').format(s.mode == 'docker' ? _('Docker 容器') : _('原生包')); },
	function(s) { return _('服务正在运行'); },
	function(s) { return _('已扫码绑定：%s').format(s.bot_id || _('未知 Bot')); },
	_('已激活，可以主动推送'),
	_('推送已开启')
];

var STEP_WHY = [
	'',
	_('微信推送要靠一个"微信机器人"服务在路由器上常驻（它负责和微信服务器保持连接），' +
	  '所以第一步是先把它装上。装完这页会自动往下走。'),
	_('这个服务第一次运行要你用微信"扫一扫"把 ClawBot 加为联系人 —— 扫完它就记住了你的微信，' +
	  '以后才能给你发消息。二维码由服务打印，约 2 分钟换一张。'),
	_('微信不允许机器人主动给"从没说过话"的人发消息。请打开微信，给这个 ClawBot 随便发一句' +
	  '（比如「你好」），服务拿到凭证后这页会自动进入下一步。'),
	_('最后一步：把服务地址和令牌写进哨兵的推送配置。点下面的按钮即可，' +
	  '写完会立刻发一条测试消息到你微信 —— 收到就说明整条链路通了。'),
	_('全部就绪。之后余额不足 / 充值恢复 / 监控失效等告警都会自动发到这个微信会话。')
];

/* ---------- 页面 ---------- */

return view.extend({
	handleSave: null,
	handleSaveApply: null,
	handleReset: null,

	load: function() {
		var wrap = function(promise) {
			return promise.catch(function(err) {
				return { error: err && err.message ? err.message : String(err) };
			});
		};
		return Promise.all([
			wrap(pfExecJson([ 'push', 'status' ])),
			wrap(pfExecJson([ 'wechat', 'status' ])),
			wrap(pfExecJson([ 'json', 'push-accounts' ]))
		]).then(function(res) {
			return { push: res[0], claw: res[1], accts: res[2] };
		});
	},

	render: function(data) {
		this.push = (data && data.push) || {};
		this.claw = (data && data.claw) || {};
		this.accts = (data && data.accts) || {};

		/* 选中的通道：默认就是当前配置的那个；没配过就从 Server酱 开始 */
		this.selected = (this.push.channel && this.push.channel != 'none')
			? this.push.channel : 'serverchan';

		this.qr = null;          /* {png_b64, hash, age} */
		this.qrAt = 0;
		this.timer = null;
		this.busy = false;
		this.formCache = {};     /* 输入框内容：重画面板时不丢用户刚填的东西 */
		this.result = null;      /* {ok, message, res} */

		this.statusNode = E('div', { 'class': 'cbi-section' });
		this.acctNode = E('div', { 'class': 'cbi-section' });
		this.selNode = E('div', { 'class': 'cbi-section' });
		this.panelNode = E('div', { 'class': 'cbi-section' });
		this.resultNode = E('div');
		this.stepNode = null;
		this.detailNode = null;
		this.errNode = null;

		this.rootNode = E('div', { 'class': 'pfw-page' }, [
			E('style', {}, STYLE),
			E('h2', {}, _('微信推送')),
			E('p', { 'class': 'pfw-why' },
				_('余额告警除了发邮件，还能推到微信。先在上面选一个通道，填好参数点「启用并测试」——' +
				  '写配置、提交、发一条测试消息一次做完，成不成当场就知道。')),
			this.statusNode,
			this.acctNode,
			this.selNode,
			this.panelNode
		]);

		this.paint();
		this.schedule(WAIT_POLL);

		return this.rootNode;
	},

	/* ---------- 定时刷新 ---------- */

	schedule: function(delay) {
		var self = this;
		window.clearTimeout(this.timer);
		this.timer = window.setTimeout(function() {
			if (!document.body.contains(self.rootNode))
				return;                                  /* 页面已切走，停掉 */
			if (self.busy) {                             /* 正在跑按钮动作，稍后再来 */
				self.schedule(2);
				return;
			}
			self.refresh(false).then(function() {
				self.schedule(self.nextDelay());
			}, function() {
				self.schedule(IDLE_POLL);
			});
		}, delay * 1000);
	},

	nextDelay: function() {
		if (this.selected == 'clawbot') {
			var step = stepOf(this.claw || {});
			if (step == 3 || step == 4)
				return WAIT_POLL;
		}
		return IDLE_POLL;
	},

	refresh: function(force) {
		var self = this;
		var pushSerial = JSON.stringify(this.push || {});
		var clawSerial = JSON.stringify(this.claw || {});
		var clawOn = (this.selected == 'clawbot');
		var qrDue = clawOn && (stepOf(this.claw || {}) == 3) && (Date.now() - this.qrAt > QR_TTL * 1000);

		var wrap = function(promise, fallback) {
			return promise.catch(function() { return fallback; });
		};

		return Promise.all([
			wrap(pfExecJson([ 'push', 'status' ]), self.push || {}),
			wrap(pfExecJson([ 'wechat', 'status' ]), self.claw || {})
		]).then(function(res) {
			var pushChanged = (JSON.stringify(res[0]) !== pushSerial);
			var clawChanged = (JSON.stringify(res[1]) !== clawSerial);
			self.push = res[0];
			self.claw = res[1];
			if (pushChanged)
				self.paintStatusCard();
			if (force || (clawOn && (clawChanged || qrDue)))
				return self.loadQrIfNeeded().then(function() { self.paintPanel(); });
		});
	},

	/* 需要二维码时取一张（ClawBot 第 3 步），否则清掉省内存 */
	loadQrIfNeeded: function() {
		var self = this;

		if (this.selected != 'clawbot' || stepOf(this.claw || {}) != 3) {
			this.qr = null;
			this.qrAt = 0;
			return Promise.resolve();
		}
		return pfExecJson([ 'wechat', 'qr', '--json' ]).then(function(res) {
			if (res && res.ok && res.png_b64) {
				if (!self.qr || self.qr.hash !== res.hash)
					self.qr = res;
				self.qrAt = Date.now();
			}
			else {
				self.qr = null;
				self.qrAt = Date.now();
			}
		}, function() {
			self.qr = null;
			self.qrAt = Date.now();
		});
	},

	/* ---------- 渲染 ---------- */

	paint: function() {
		this.paintStatusCard();
		this.paintAccounts();
		this.paintSelector();
		this.paintPanel();
	},

	paintStatusCard: function() {
		var p = this.push || {};
		var self = this;

		if (p.error) {
			dom.content(this.statusNode, E('div', { 'class': 'alert-message danger' }, [
				E('h4', {}, _('读不到推送状态')),
				E('p', {}, p.error),
				E('p', {}, _('多半是这两种情况：① 没装 python3（推送通道管理需要一个 python3 助手）；' +
					'② powerfee 还是旧版本（没有 push 子命令，升级到 1.0.3 即可）。'))
			]));
			return;
		}

		var chName = p.channel_name || _('未配置');
		var chNode = (p.channel && p.channel != 'none')
			? E('span', {}, [ badge(chName, p.enabled ? COLOR.ok : COLOR.dim),
				p.enabled ? '' : E('span', { 'class': 'pfw-note' }, ' ' + _('（已关闭）')) ])
			: E('span', { 'class': 'pfw-note' }, _('还没配置推送通道'));

		dom.content(this.statusNode, [
			E('h3', {}, _('当前状态')),
			E('table', { 'class': 'pfw-table' }, [
				row(_('推送通道'), chNode),
				row(_('推送开关'), p.enabled ? badge(_('已开启'), COLOR.ok) : badge(_('已关闭'), COLOR.err)),
				row(_('推送地址'), p.url_masked
					? E('span', { 'style': 'word-break:break-all' }, p.url_masked)
					: E('span', { 'class': 'pfw-note' }, _('（未配置）'))),
				row(_('密钥'), p.key_hint
					? E('span', {}, p.key_hint)
					: E('span', { 'class': 'pfw-note' }, _('—'))),
				row(_('最近一次推送'), p.last_push
					? E('span', { 'style': 'word-break:break-all' }, p.last_push)
					: E('span', { 'class': 'pfw-note' }, _('（日志里还没有推送记录）')))
			]),
			E('div', { 'class': 'pfw-actions' }, [
				E('button', {
					'class': 'cbi-button cbi-button-action',
					'click': ui.createHandlerFn(this, 'handleTest')
				}, _('发测试消息')),
				' ',
				E('button', {
					'class': 'cbi-button',
					'click': ui.createHandlerFn(this, 'handleRefresh')
				}, _('刷新状态')),
				' ',
				E('a', {
					'class': 'cbi-button',
					'href': L.url('admin/services/powerfee/settings')
				}, _('去设置页微调'))
			])
		]);
	},

	/* ---------- 推送账号（命名账号：给不同房间指定不同推送目标） ---------- */

	paintAccounts: function() {
		var self = this;
		var res = this.accts || {};
		var list = accountList(res);
		var nodes = [];

		nodes.push(E('h3', {}, _('推送账号')));
		nodes.push(E('p', { 'class': 'pfw-why' },
			_('下面是命名推送账号（<code>config push_account</code>）：想让不同房间推到不同的微信 / QQ / 群机器人，' +
			  '就在这里建几个账号，再到「宿舍管理」页给每个房间选一个（房间里的 <code>notify_account</code> 引用账号名）。' +
			  '没有单独指定账号的房间，用的就是上面「推送通道」里的默认配置（<code>notify</code> 段）。')));

		if (!list) {
			nodes.push(E('div', { 'class': 'alert-message warning' }, [
				E('h4', {}, _('读不到推送账号列表')),
				E('p', {}, String(res.error || _('powerfee 没有返回账号列表。'))),
				E('p', {}, _('多半是 powerfee 还是旧版本（没有 push-account 子命令）；升级到 1.2.0 后这里就能管理多个推送账号。'))
			]));
			nodes.push(E('p', {}, E('button', {
				'class': 'cbi-button',
				'click': ui.createHandlerFn(this, 'handleAccountsRefresh')
			}, _('重新读取'))));
			dom.content(this.acctNode, nodes);
			return;
		}

		nodes.push(E('p', {}, [
			E('button', {
				'class': 'cbi-button cbi-button-add',
				'click': ui.createHandlerFn(this, 'handleAccountAdd')
			}, _('添加推送账号')),
			' ',
			E('button', {
				'class': 'cbi-button',
				'click': ui.createHandlerFn(this, 'handleAccountsRefresh')
			}, _('刷新列表'))
		]));

		/* 引擎回的账号问题清单（缺地址 / 停用 / 引用了不存在的账号等） */
		var issues = Array.isArray(res.issues) ? res.issues.filter(function(it) {
			return it && it.kind !== 'mail' && it.kind !== 'mail_account';
		}) : [];
		if (issues.length) {
			nodes.push(E('div', { 'class': 'alert-message warning' }, [
				E('h4', {}, _('有账号配置不完整')),
				E('ul', {}, issues.map(function(it) {
					return E('li', {}, '%s：%s'.format(String(it.name || ''), String(it.message || '')));
				}))
			]));
		}

		if (!list.length) {
			nodes.push(E('p', {}, E('em', {}, _('还没有推送账号，所有房间都用上面的默认通道。'))));
			dom.content(this.acctNode, nodes);
			return;
		}

		var rows = [ E('tr', { 'class': 'tr table-titles' }, [
			E('th', {}, _('名称')),
			E('th', {}, _('通道')),
			E('th', {}, _('地址（掩码）')),
			E('th', {}, _('状态')),
			E('th', { 'class': 'right' }, _('操作'))
		]) ];

		list.forEach(function(a) {
			var name = accName(a);
			var chRaw = String(acctRaw(a, [ 'channel' ]) || '');
			var chEff = String(acctEff(a, [ 'channel' ]) || '');
			var chEffName = acctEff(a, [ 'channel_name' ]);
			var url = acctEff(a, [ 'url_masked' ]);
			var enabled = !accDisabled(a);
			var usable = accountUsable(a);
			var issue = firstVal(a, [ 'issue' ]);
			var usedBy = (a && Array.isArray(a.used_by)) ? a.used_by : null;
			var chText = chRaw ? accountChannelTitle(chRaw)
				: (chEff ? _('跟随默认（%s）').format(chEffName || accountChannelTitle(chEff))
					: _('跟随默认'));

			rows.push(E('tr', { 'class': 'tr' }, [
				E('td', {}, [
					E('strong', {}, name || _('（未命名）')),
					usedBy
						? E('div', { 'class': 'pfw-note' },
							usedBy.length ? _('用于：%s').format(usedBy.join('、')) : _('（没有房间在用）'))
						: ''
				]),
				E('td', {}, chText),
				E('td', { 'style': 'font-size:12px;word-break:break-all' }, url ? String(url)
					: ((chRaw === 'clawbot' || (!chRaw && chEff === 'clawbot'))
						? _('本机 ClawBot API（地址留空）')
						: E('span', { 'class': 'pfw-note' }, _('未配置（留空继承 notify.url）')))),
				E('td', {}, [
					enabled ? badge(_('已启用'), COLOR.ok) : badge(_('已停用'), COLOR.err),
					(usable === false && enabled)
						? ' ' + E('span', { 'class': 'pfw-note' }, _('（不可用）'))
						: '',
					(issue && usable === false)
						? E('div', { 'class': 'pfw-note' }, String(issue))
						: ''
				]),
				E('td', { 'class': 'right', 'style': 'white-space:nowrap' }, [
					E('button', {
						'class': 'cbi-button cbi-button-action',
						'click': ui.createHandlerFn(self, 'handleAccountEdit', a)
					}, _('编辑')),
					' ',
					E('button', {
						'class': 'cbi-button',
						'click': ui.createHandlerFn(self, 'handleAccountTest', a)
					}, _('发测试消息')),
					' ',
					E('button', {
						'class': 'cbi-button cbi-button-remove',
						'click': ui.createHandlerFn(self, 'handleAccountRemove', a)
					}, _('删除'))
				])
			]));
		});

		nodes.push(E('table', { 'class': 'table' }, rows));
		nodes.push(E('p', { 'class': 'pfw-note' },
			_('令牌（token）只显示「已设置」提示，不会回显真值；编辑时留空表示不修改。' +
			  '账号里留空的字段 = 继承 <code>notify</code> 段的同名字段（enabled 除外，默认启用）。')));

		dom.content(this.acctNode, nodes);
	},

	refreshAccounts: function() {
		var self = this;
		return pfExecJson([ 'json', 'push-accounts' ]).then(function(res) {
			self.accts = res || {};
			self.paintAccounts();
		}, function(err) {
			self.accts = { error: (err && err.message) ? err.message : String(err) };
			self.paintAccounts();
		});
	},

	handleAccountsRefresh: function(ev) {
		return withBusy(_('正在刷新…'), this.refreshAccounts()).catch(notifyError);
	},

	handleAccountAdd: function(ev) {
		this.showAccountModal(null);
	},

	handleAccountEdit: function() {
		var account = handlerArgs(arguments)[0];
		this.showAccountModal(account);
	},

	handleAccountTest: function() {
		var account = handlerArgs(arguments)[0];
		var name = accName(account);
		return withBusy(_('正在发送测试消息…'), pfRun([ 'push-account', 'test', name ])).then(function(res) {
			var ok = !(res && res.ok === false);
			var msg = (res && (res.message || res.error)) || _('测试消息已发送。');
			ui.addNotification(null, E('p', {}, (ok ? _('成功：') : _('失败：')) + msg), ok ? 'success' : 'danger');
		}).catch(notifyError);
	},

	handleAccountRemove: function() {
		var account = handlerArgs(arguments)[0];
		var self = this;
		var name = accName(account);

		ui.showModal(_('删除推送账号'), [
			E('p', {}, _('确定删除推送账号「%s」吗？').format(name)),
			E('p', { 'class': 'pfw-note' }, _('引用它的房间会自动回到默认通道（notify 段），不会影响别的账号。')),
			E('div', { 'class': 'right' }, [
				E('button', { 'class': 'btn cbi-button cbi-button-neutral', 'click': ui.hideModal }, _('取消')),
				' ',
				E('button', {
					'class': 'btn cbi-button cbi-button-remove',
					'click': function(ev) {
						ui.hideModal();
						withBusy(_('正在删除…'), pfRun([ 'push-account', 'remove', name ])).then(function(res) {
							var ok = !(res && res.ok === false);
							var msg = (res && (res.message || res.error)) || _('已删除。');
							ui.addNotification(null, E('p', {}, (ok ? '' : _('失败：')) + msg), ok ? 'success' : 'danger');
							return self.refreshAccounts();
						}).catch(function(err) {
							notifyError(err);
							return self.refreshAccounts();
						});
					}
				}, _('删除'))
			])
		]);
	},

	/* 新增 / 编辑弹窗：密钥只显示掩码提示，输入框只收新值 */
	showAccountModal: function(account) {
		var self = this;
		var a = account || {};
		var isNew = !account;
		var fields = {};
		var prefill = {};

		function rowOf(label, node, hint) {
			return E('div', { 'class': 'cbi-value' }, [
				E('label', { 'class': 'cbi-value-title' }, label),
				E('div', { 'class': 'cbi-value-field' }, [
					node,
					hint ? E('div', { 'class': 'cbi-value-description' }, hint) : ''
				])
			]);
		}

		function textRow(key, label, opts) {
			opts = opts || {};
			/* 密钥字段永远不回显；其余字段回显「账号自己写的那份」（raw，空 = 继承） */
			var v = opts.secret ? '' : acctRaw(a, [ key ]);
			prefill[key] = (v === '' ? '' : String(v));
			var input = E('input', {
				'type': opts.secret ? 'password' : 'text',
				'class': 'cbi-input-text',
				'value': prefill[key],
				'placeholder': opts.placeholder || '',
				'spellcheck': 'false',
				'autocomplete': 'off'
			});
			fields[key] = input;
			var line = [ input ];
			if (opts.secret) {
				var btn = E('button', {
					'class': 'cbi-button',
					'style': 'margin-left:6px',
					'click': function(ev) {
						ev.preventDefault();
						var showing = (input.type === 'text');
						input.type = showing ? 'password' : 'text';
						btn.textContent = showing ? _('显示') : _('隐藏');
					}
				}, _('显示'));
				line.push(btn);
			}
			return rowOf(label, E('div', {}, line), opts.hint);
		}

		function selectRow(key, label, options, hint) {
			var v = acctRaw(a, [ key ]);
			prefill[key] = (v === '' ? '' : String(v));
			var sel = E('select', { 'class': 'cbi-input-select' });
			options.forEach(function(o) {
				sel.appendChild(E('option', {
					'value': o[0],
					'selected': (o[0] === prefill[key]) ? '' : null
				}, o[1]));
			});
			fields[key] = sel;
			return rowOf(label, sel, hint);
		}

		var nameInput = null;
		var nameNode;
		if (isNew) {
			nameInput = E('input', { 'type': 'text', 'class': 'cbi-input-text', 'placeholder': 'sc_room1' });
			nameNode = nameInput;
		}
		else {
			nameNode = E('strong', {}, accName(a) || _('（未命名）'));
		}

		prefill.enabled = accDisabled(a) ? '0' : '1';
		fields.enabled = E('input', { 'type': 'checkbox' });
		if (prefill.enabled === '1')
			fields.enabled.checked = true;

		prefill.body = String(acctRaw(a, [ 'body' ]) || '');
		fields.body = E('textarea', {
			'class': 'cbi-input-textarea',
			'rows': 3,
			'spellcheck': 'false'
		}, prefill.body);

		var clearToken = E('input', { 'type': 'checkbox' });
		var clearUrl = E('input', { 'type': 'checkbox' });

		var tokHint = acctEff(a, [ 'token_hint', 'token_masked' ]);
		var tokSet = acctBool(a, [ 'token_set', 'has_token', 'token_configured' ]);
		if (typeof tokHint === 'boolean') {
			if (tokSet === null)
				tokSet = tokHint;
			tokHint = '';
		}
		tokHint = String(tokHint || '');
		var urlMasked = acctEff(a, [ 'url_masked' ]);

		/* 只把「用户改过的字段」发出去：列表里没回的字段不会因此被清空 */
		function collect() {
			var kvs = [];
			Object.keys(fields).forEach(function(key) {
				if (key === 'token')
					return;                 /* 单独处理 */
				var el = fields[key];
				var v;
				if (el.type === 'checkbox')
					v = el.checked ? '1' : '0';
				else if (el.tagName === 'TEXTAREA')
					v = String(el.value || '');         /* 模板里的空白有意义，不 trim */
				else
					v = String(el.value || '').trim();
				if (isNew || v !== prefill[key])
					kvs.push([ key, v ]);
			});
			var tv = String(fields.token.value || '');
			if (tv)
				kvs.push([ 'token', tv ]);
			else if (clearToken.checked)
				kvs.push([ 'token', '' ]);
			if (clearUrl.checked)
				kvs.push([ 'url', '' ]);
			return kvs;
		}

		function submit(ev, andTest) {
			var name = nameInput ? String(nameInput.value || '').trim() : accName(a);
			if (!name) {
				ui.addNotification(null, E('p', {}, _('请先给账号起个名字（段名），例如 sc_room1。')), 'warning');
				return;
			}
			if (!/^[A-Za-z0-9_]+$/.test(name)) {
				ui.addNotification(null, E('p', {}, _('名字只能用字母、数字和下划线。')), 'warning');
				return;
			}
			ui.hideModal();
			return self.saveAccount(name, isNew, collect(), andTest);
		}

		ui.showModal(isNew ? _('添加推送账号') : _('编辑推送账号：%s').format(accName(a)), [
			E('div', { 'class': 'cbi-map' }, [
				rowOf(_('账号名（段名）'), nameNode,
					_('只用字母、数字、下划线，例如 sc_room1；房间在「宿舍管理」页按这个名字引用。')),
				rowOf(_('启用'),
					E('label', {}, [ fields.enabled, ' ', _('启用这个账号') ])),
				selectRow('channel', _('推送通道'), ACCOUNT_CHANNELS.map(function(c) {
					return [ c.value, c.title ];
				}), _('留空 = 继承 notify 段的通道；ClawBot 通道且地址留空时，自动用本机 ClawBot API。')),
				textRow('url', _('推送地址（URL）'), {
					placeholder: 'https://example.com/webhook',
					hint: urlMasked
						? _('当前生效：%s（掩码）。留空 = 不修改，填新的会覆盖；本账号自己没写地址时继承 notify.url。').format(urlMasked)
						: _('留空继承 notify.url；ClawBot 通道且地址留空时用本机 ClawBot API。')
				}),
				E('div', { 'class': 'cbi-value' }, [
					E('label', { 'class': 'cbi-value-title' }, ''),
					E('div', { 'class': 'cbi-value-field' },
						E('label', {}, [ clearUrl, ' ', _('清除本账号的推送地址（改回继承 notify.url）') ]))
				]),
				selectRow('method', _('请求方法'), [
					[ '', _('继承默认（notify.method）') ],
					[ 'POST', 'POST' ],
					[ 'GET', 'GET' ]
				]),
				textRow('content_type', _('Content-Type（POST）'), { placeholder: 'application/json' }),
				rowOf(_('请求体模板'), fields.body,
					_('占位符：{text} 单行摘要、{title} 标题、{room} 房间、{balance} 余额、{unit} 单位、' +
					  '{level} 档位、{reason} 原因、{daily} 日均、{days_left} 可用天数、{time} 时间、{device} 设备名。' +
					  'JSON 里不能有裸换行，要换行请写 \\n。留空继承 notify.body。')),
				textRow('token', _('鉴权令牌'), {
					secret: true,
					placeholder: tokSet ? _('已设置（填新的会覆盖）') : _('留空 = 不修改'),
					hint: tokHint ? _('已保存：%s').format(tokHint)
						: (tokSet ? _('已保存一个令牌（不会回显）。') : _('还没有令牌（留空则不发送鉴权头）。'))
				}),
				E('div', { 'class': 'cbi-value' }, [
					E('label', { 'class': 'cbi-value-title' }, ''),
					E('div', { 'class': 'cbi-value-field' },
						E('label', {}, [ clearToken, ' ', _('清除本账号的鉴权令牌（改回继承 notify.token）') ]))
				]),
				textRow('token_header', _('鉴权请求头'), { placeholder: 'Authorization' }),
				textRow('timeout', _('推送超时（秒）'), { placeholder: '15' }),
				textRow('bot_id', _('ClawBot BotID'), {
					placeholder: _('如 bot_xxxxxxxx'),
					hint: _('只有 ClawBot 通道需要；留空继承本机 ClawBot 服务里已激活的 Bot。')
				})
			]),
			E('div', { 'class': 'right' }, [
				E('button', { 'class': 'btn cbi-button cbi-button-neutral', 'click': ui.hideModal }, _('取消')),
				' ',
				E('button', {
					'class': 'btn cbi-button cbi-button-save',
					'click': function(ev) {
						ev.preventDefault();
						return submit(ev, false);
					}
				}, _('保存')),
				' ',
				E('button', {
					'class': 'btn cbi-button cbi-button-save important',
					'click': function(ev) {
						ev.preventDefault();
						return submit(ev, true);
					}
				}, _('保存并测试'))
			])
		]);
	},

	/* 保存 = 若干条 push-account 子命令（add + 逐个 set），保存后刷新列表 */
	saveAccount: function(name, isNew, kvs, andTest) {
		var self = this;
		var cmds = [];
		if (isNew)
			cmds.push([ 'add', name ]);
		kvs.forEach(function(kv) {
			cmds.push([ 'set', name, kv[0], kv[1] ]);
		});

		var run = cmds.reduce(function(p, cmd) {
			return p.then(function() {
				return pfRun([ 'push-account' ].concat(cmd));
			});
		}, Promise.resolve());

		return withBusy(_('正在保存推送账号…'), run).then(function() {
			ui.addNotification(null, E('p', {}, _('已保存推送账号「%s」。').format(name)), 'success');
			if (!andTest)
				return null;
			return withBusy(_('正在发送测试消息…'), pfRun([ 'push-account', 'test', name ])).then(function(res) {
				var ok = !(res && res.ok === false);
				var msg = (res && (res.message || res.error)) || _('测试消息已发送。');
				ui.addNotification(null, E('p', {}, (ok ? _('成功：') : _('失败：')) + msg), ok ? 'success' : 'danger');
			});
		}).then(function() {
			return self.refreshAccounts();
		}).catch(function(err) {
			notifyError(err);
			return self.refreshAccounts();
		});
	},

	paintSelector: function() {
		var self = this;
		var p = this.push || {};
		var box = E('div', { 'class': 'pfw-chans' });

		CHANNELS.forEach(function(ch) {
			var active = (self.selected == ch.key);
			var isCurrent = (p.channel == ch.key);
			box.appendChild(E('button', {
				'class': 'pfw-chan' + (active ? ' active' : ''),
				'click': function(ev) {
					ev.preventDefault();
					self.selectChannel(ch.key);
				}
			}, isCurrent ? ch.title + ' ●' : ch.title));
		});

		var cur = CHANNELS.filter(function(c) { return c.key == self.selected; })[0];

		dom.content(this.selNode, [
			E('h3', {}, _('推送通道')),
			box,
			E('p', { 'class': 'pfw-why' }, cur ? cur.desc : '')
		]);
	},

	/* 切换通道：只换下半部分的表单，状态卡不动 */
	selectChannel: function(key) {
		if (this.selected == key)
			return;
		this.captureForm();
		this.selected = key;
		this.result = null;
		this.qr = null;
		this.qrAt = 0;
		this.paintSelector();
		this.paintPanel();
		if (key == 'clawbot')
			this.refresh(true);
	},

	paintPanel: function() {
		var keep = this.captureForm();
		var nodes = [];

		switch (this.selected) {
		case 'serverchan': nodes = this.buildServerchan(); break;
		case 'wecom': nodes = this.buildWecom(); break;
		case 'clawbot': nodes = this.buildClawbot(); break;
		case 'webhook': nodes = this.buildWebhook(); break;
		default: nodes = this.buildOff(); break;
		}

		dom.content(this.panelNode, nodes);
		this.restoreForm(keep);
		this.paintResult();
	},

	/* 输入框内容在重画/切换通道时保留（比如测试失败后想改一下再试，或者切走再切回来） */
	captureForm: function() {
		var keep = this.formCache || {};
		if (this.panelNode) {
			Array.prototype.forEach.call(this.panelNode.querySelectorAll('input,textarea,select'), function(el) {
				if (el.id)
					keep[el.id] = el.value;
			});
		}
		this.formCache = keep;
		return keep;
	},

	restoreForm: function(keep) {
		if (!keep)
			return;
		Array.prototype.forEach.call(this.panelNode.querySelectorAll('input,textarea,select'), function(el) {
			if (el.id && keep[el.id] !== undefined)
				el.value = keep[el.id];
		});
	},

	clearField: function(id) {
		if (this.formCache)
			delete this.formCache[id];
		var el = document.getElementById(id);
		if (el)
			el.value = '';
	},

	paintResult: function() {
		if (!this.resultNode)
			return;
		var r = this.result;
		if (!r) {
			dom.content(this.resultNode, '');
			return;
		}
		dom.content(this.resultNode, E('div', { 'class': 'pfw-res ' + (r.ok ? 'ok' : 'err') }, [
			E('strong', {}, r.ok ? _('成功：') : _('失败：')),
			E('span', {}, r.message),
			(r.res && r.res.last_push)
				? E('div', { 'class': 'pfw-note', 'style': 'margin-top:6px' },
					_('最近一次推送：%s').format(r.res.last_push))
				: ''
		]));
	},

	showResult: function(ok, msg, res) {
		this.result = { ok: !!ok, message: msg || '', res: res || null };
		this.paintResult();
	},

	/* ---------- 各通道的表单 ---------- */

	buildServerchan: function() {
		var p = this.push || {};
		var hint = p.key_set
			? _('已保存：%s。输入框里填新的 SendKey 会覆盖它；不填则保持不动。').format(p.key_hint)
			: _('还没填 SendKey —— 填一个再点下面的按钮。');

		return [
			E('h3', {}, _('Server酱')),
			E('p', { 'class': 'pfw-why' },
				_('Server酱 是一个"扫码就能给自己微信发消息"的推送服务：在 sct.ftqq.com 微信扫码登录，' +
				  '复制页面上的 SendKey 填到这里就行，路由器上不用装任何东西。')),
			E('p', { 'class': 'pfw-note' }, hint),
			field(_('SendKey'), 'pfw-key', {
				secret: true,
				placeholder: p.key_set ? _('已保存（填新的会覆盖）') : 'SCT… / sctp…'
			}),
			E('div', { 'class': 'pfw-actions' }, [
				E('button', {
					'class': 'cbi-button cbi-button-action important',
					'click': ui.createHandlerFn(this, 'handleSetServerchan')
				}, _('启用并测试'))
			]),
			E('p', { 'class': 'pfw-note' },
				_('SendKey 在 sct.ftqq.com 微信扫码登录后获取；免费版每天有额度，个人收告警够用。' +
				  '点上面的按钮会写入配置、提交，并立刻给你发一条测试消息 —— 微信里收到就说明通了。')),
			this.resultNode
		];
	},

	buildWecom: function() {
		var p = this.push || {};
		var hint = (p.channel == 'wecom' && p.key_set)
			? _('已保存：%s。要换群就粘一条新的地址进来。').format(p.key_hint)
			: _('把群机器人的 Webhook 地址整条粘进来（形如 https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=…）。');

		return [
			E('h3', {}, _('企业微信群机器人')),
			E('p', { 'class': 'pfw-why' },
				_('在企业的微信群里加一个"群机器人"，告警就会以它的名义发到那个群 —— 适合宿舍几个人一起看。')),
			E('p', { 'class': 'pfw-note' }, hint),
			field(_('Webhook 地址'), 'pfw-wecom-url', {
				secret: true,
				placeholder: 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=…'
			}),
			E('div', { 'class': 'pfw-actions' }, [
				E('button', {
					'class': 'cbi-button cbi-button-action important',
					'click': ui.createHandlerFn(this, 'handleSetWecom')
				}, _('启用并测试'))
			]),
			E('p', { 'class': 'pfw-note' },
				_('获取方式：在企业微信里打开目标群 → 右上角「…」→ 群机器人 → 添加机器人 → ' +
				  '复制它的 Webhook 地址。注意群机器人只能在企业微信的群里用，个人微信群不行。')),
			this.resultNode
		];
	},

	buildWebhook: function() {
		var p = this.push || {};
		var isCur = (p.channel == 'webhook');
		var url = '';
		var method = isCur ? (p.method || 'POST') : 'POST';
		var ctype = isCur ? (p.content_type || 'application/json') : 'application/json';
		var body = (isCur && p.body) ? p.body : '{"text":"{text}"}';

		return [
			E('h3', {}, _('自定义 webhook')),
			E('p', { 'class': 'pfw-why' },
				_('任何能收 HTTP 请求的地址都能接：Bark、ntfy、钉钉、飞书、自建脚本……' +
				  '下面四个字段会原样写进哨兵的推送配置。')),
			E('p', { 'class': 'pfw-note' },
				isCur && p.url_masked
					? _('当前已配置：%s').format(p.url_masked)
					: _('当前没有配置自定义 webhook。')),
			field(_('地址（URL）'), 'pfw-wh-url', { placeholder: 'https://example.com/hook' }),
			selectField(_('请求方式'), 'pfw-wh-method', [ 'POST', 'GET' ], method),
			field(_('Content-Type'), 'pfw-wh-ctype', { value: ctype }),
			textareaField(_('请求体模板'), 'pfw-wh-body', body,
				_('占位符：{text} 单行摘要、{title} 标题、{room} 房间、{balance} 余额、{unit} 单位、' +
				  '{level} 档位、{reason} 原因、{daily} 日均、{days_left} 可用天数、{time} 时间、{device} 设备名。' +
				  'JSON 里不能有裸换行，要换行请写 \\n。')),
			E('div', { 'class': 'pfw-actions' }, [
				E('button', {
					'class': 'cbi-button cbi-button-action important',
					'click': ui.createHandlerFn(this, 'handleSetWebhook')
				}, _('启用并测试'))
			]),
			E('p', { 'class': 'pfw-note' },
				_('要额外加鉴权请求头（比如 X-Auth-Token）请去「设置 → 通知推送」里配，这里只写上面四个字段。')),
			this.resultNode
		];
	},

	buildOff: function() {
		var p = this.push || {};
		return [
			E('h3', {}, _('关闭推送')),
			E('p', { 'class': 'pfw-why' },
				_('关掉之后哨兵不再往任何地方推通知（余额监控、日志、邮件提醒都不受影响，' +
				  '邮件是另一套开关）。')),
			E('p', { 'class': 'pfw-note' },
				_('当前：%s，%s。').format(p.channel_name || _('未配置通道'),
					p.enabled ? _('推送是开着的') : _('推送已经是关的')) +
				_('通道参数会保留，想重新开就在上面选回那个通道。')),
			E('div', { 'class': 'pfw-actions' }, [
				E('button', {
					'class': 'cbi-button cbi-button-negative',
					'click': ui.createHandlerFn(this, 'handleOff')
				}, _('关闭推送'))
			]),
			this.resultNode
		];
	},

	buildClawbot: function() {
		this.errNode = E('div');
		this.stepNode = E('div', { 'class': 'cbi-section' });
		this.detailNode = E('div', { 'class': 'cbi-section' });
		this.paintClawbot();
		return [ this.errNode, this.stepNode, this.detailNode ];
	},

	/* ClawBot 五步向导（原「微信推送」页的主体，搬到这里按通道展示） */
	paintClawbot: function() {
		if (!this.stepNode)
			return;
		var s = this.claw || {};
		var self = this;

		if (s.error) {
			dom.content(this.errNode, E('div', { 'class': 'alert-message danger' }, [
				E('h4', {}, _('无法读取微信服务状态')),
				E('p', {}, s.error),
				E('p', {}, _('多半是这两种情况：① 没装 python3（本页需要一个 python3 助手）；' +
					'② 权限没生效（ACL 里要有执行 /usr/bin/powerfee）。'))
			]));
			dom.content(this.stepNode, E('p', {}, E('em', {}, _('读取中…'))));
			dom.content(this.detailNode, '');
			return;
		}

		dom.content(this.errNode, '');

		var step = stepOf(s);
		var steps = E('ul', { 'class': 'pfw-steps' });
		for (var i = 1; i <= 5; i++) {
			var cls = (i < step) ? 'done' : (i == step ? 'current' : '');
			var mark = (i < step) ? '✓' : (i == step ? '▶' : '○');
			var text = STEP_TITLE[i];
			if (i < step) {
				var d = STEP_DONE[i];
				text += '：' + (typeof(d) === 'function' ? d(s) : d);
			}
			steps.appendChild(E('li', { 'class': cls }, '%s %s'.format(mark, text)));
		}

		var card = E('div', { 'class': 'pfw-step' });
		switch (step) {
		case 1: this.paintInstall(card); break;
		case 2: this.paintStart(card); break;
		case 3: this.paintQr(card); break;
		case 4: this.paintActivate(card); break;
		case 5: this.paintEnable(card); break;
		default: this.paintReady(card); break;
		}

		dom.content(this.stepNode, [
			E('h3', {}, _('ClawBot 配置进度')),
			steps,
			E('h3', {}, STEP_TITLE[step]),
			E('p', { 'class': 'pfw-why' }, STEP_WHY[step]),
			card
		]);

		this.paintDetail();
	},

	paintInstall: function(card) {
		card.appendChild(E('p', {}, _('路由器上还没有微信服务。下面任选一种装上（原生包更省内存，推荐）：')));

		card.appendChild(E('p', { 'class': 'pfw-note' }, _('OpenWrt 25.x（apk）：')));
		card.appendChild(cmdBlock('apk add weclawbot-api\n/etc/init.d/weclawbot-api enable && /etc/init.d/weclawbot-api start'));

		card.appendChild(E('p', { 'class': 'pfw-note' }, _('OpenWrt 24.10 及以下（opkg）：')));
		card.appendChild(cmdBlock('opkg install weclawbot-api\n/etc/init.d/weclawbot-api enable && /etc/init.d/weclawbot-api start'));

		card.appendChild(E('p', { 'class': 'pfw-note' },
			_('没有原生包时的过渡方案（Docker）：')));
		card.appendChild(cmdBlock('docker run -d --name weclawbot-api --restart unless-stopped \\\n' +
			'  -p 26322:26322 -v /opt/weclawbot/config:/app/config \\\n' +
			'  cp0204/weclawbot-api:latest'));

		card.appendChild(E('p', { 'class': 'pfw-note' },
			_('装好后不用刷新页面，这页会自己发现服务并进入下一步。')));
	},

	paintStart: function(card) {
		var s = this.claw || {};

		card.appendChild(E('p', {}, _('服务已安装（%s），但还没有在运行。')
			.format(s.mode == 'docker' ? _('Docker 容器') : _('原生包'))));

		if (s.mode == 'docker') {
			card.appendChild(E('p', { 'class': 'pfw-note' }, _('Docker 形态请手动执行：')));
			card.appendChild(cmdBlock('docker start weclawbot-api'));
		}
		else {
			card.appendChild(E('button', {
				'class': 'cbi-button cbi-button-apply',
				'click': ui.createHandlerFn(this, 'handleStart')
			}, _('启动微信服务')));
			card.appendChild(E('p', { 'class': 'pfw-note' },
				_('启动后服务会打印登录二维码，这页会立刻显示出来。')));
		}
	},

	paintQr: function(card) {
		var s = this.claw || {};

		card.appendChild(E('p', {}, _('打开微信 →「扫一扫」，扫下面这张二维码，把 ClawBot 加为联系人。')));

		if (this.qr && this.qr.png_b64) {
			card.appendChild(E('div', { 'class': 'pfw-qr' }, E('img', {
				'src': 'data:image/png;base64,' + this.qr.png_b64,
				'alt': _('微信登录二维码')
			})));
			card.appendChild(E('p', { 'class': 'pfw-note' },
				_('二维码约 2 分钟过期（服务会自己换新的，这页每 90 秒自动刷新一次）%s')
					.format(this.qr.age != null ? _('；当前这张约 %s 秒前生成').format(this.qr.age) : '')));
		}
		else {
			card.appendChild(E('div', { 'class': 'alert-message warning' }, [
				E('p', {}, s.qr_stale
					? _('日志里只剩一张旧的二维码（服务可能已经登录过），扫它没用。')
					: _('暂时没拿到二维码：服务可能还在启动。')),
				E('p', { 'class': 'pfw-note' }, _('等十几秒再看；如果一直没有，可以重启服务让它重新打印二维码。'))
			]));
		}

		card.appendChild(E('div', {}, [
			E('button', {
				'class': 'cbi-button cbi-button-action',
				'click': ui.createHandlerFn(this, 'handleQrRefresh')
			}, _('刷新二维码')),
			' ',
			(s.mode == 'docker' && s.running)
				? copyButton('docker restart weclawbot-api', _('复制重启命令'))
				: E('button', {
					'class': 'cbi-button',
					'click': ui.createHandlerFn(this, 'handleRestart')
				}, _('重启服务'))
		]));

		card.appendChild(E('p', { 'class': 'pfw-note' },
			_('扫码成功后这页会自动跳到下一步（不用手动刷新）。')));
	},

	paintActivate: function(card) {
		var s = this.claw || {};

		card.appendChild(E('div', { 'class': 'alert-message warning' }, [
			E('p', {}, [
				_('已经绑定 Bot '),
				E('strong', {}, s.bot_id || _('未知')),
				_('，但它还没有「激活」。')
			]),
			E('p', {}, _('请打开微信，找到这个 ClawBot 的聊天窗口，随便发一句话（例如「你好」）。'))
		]));

		card.appendChild(E('p', { 'class': 'pfw-note' },
			_('页面每 5 秒检查一次，发完消息几秒内就会自动进入下一步。')));

		card.appendChild(E('button', {
			'class': 'cbi-button cbi-button-neutral',
			'click': ui.createHandlerFn(this, 'handleQrRefresh')
		}, _('重新检查')));
	},

	paintEnable: function(card) {
		var s = this.claw || {};

		card.appendChild(E('p', {}, [
			_('微信服务已就绪（Bot：'),
			E('strong', {}, s.bot_id || _('未知')),
			_('），只差把推送配置写好。')
		]));

		card.appendChild(E('button', {
			'class': 'cbi-button cbi-button-action important',
			'click': ui.createHandlerFn(this, 'handleEnable')
		}, _('一键启用微信推送')));

		card.appendChild(E('p', { 'class': 'pfw-note' },
			_('会做三件事：读取服务凭据 → 写入哨兵的推送配置（uci commit）→ 立刻发一条测试消息。' +
			  '收到测试消息就说明配置成功。')));

		if (s.notify_enabled && !s.notify_url_ok)
			card.appendChild(E('p', { 'class': 'pfw-note' },
				_('（检测到推送地址和当前 Bot 对不上，一键启用会把它改对。）')));
	},

	paintReady: function(card) {
		var s = this.claw || {};

		card.appendChild(E('p', {}, [
			badge(_('ClawBot 推送已就绪'), COLOR.ok),
			' ',
			E('span', { 'class': 'pfw-note' }, _('余额告警会自动发到你的微信会话。'))
		]));

		card.appendChild(E('table', { 'class': 'pfw-table' }, [
			row(_('BotID'), s.bot_id || '—'),
			row(_('是否已激活'), s.context_ready ? badge(_('已激活'), COLOR.ok) : badge(_('未激活'), COLOR.warn)),
			row(_('推送开关'), s.notify_enabled ? badge(_('已开启'), COLOR.ok) : badge(_('未开启'), COLOR.err)),
			row(_('最近一次推送'), s.last_push || E('span', { 'class': 'pfw-note' }, _('（日志里还没有推送记录）')))
		]));

		card.appendChild(E('div', { 'style': 'margin-top:8px' }, [
			E('button', {
				'class': 'cbi-button cbi-button-action',
				'click': ui.createHandlerFn(this, 'handleTest')
			}, _('发测试消息')),
			' ',
			E('button', {
				'class': 'cbi-button',
				'click': ui.createHandlerFn(this, 'handleEnable')
			}, _('重新一键配置'))
		]));

		card.appendChild(E('p', { 'class': 'pfw-note' },
			_('想改推送内容模板（{text} 等占位符）或换成别的 webhook，去「设置 → 通知推送」。')));
	},

	paintDetail: function() {
		if (!this.detailNode)
			return;
		var s = this.claw || {};
		if (s.error) {
			dom.content(this.detailNode, '');
			return;
		}

		var mode = (s.mode == 'native') ? _('原生包（/usr/bin/weclawbot-api）')
			: (s.mode == 'docker') ? _('Docker 容器（weclawbot-api）')
			: _('未检测到');

		dom.content(this.detailNode, [
			E('h3', {}, _('ClawBot 详情')),
			E('table', { 'class': 'pfw-table' }, [
				row(_('服务形态'), mode),
				row(_('服务状态'), s.running ? badge(_('运行中'), COLOR.ok) : badge(_('未运行'), COLOR.dim)),
				row(_('已登录 Bot 数'), '%s'.format(s.bot_count == null ? '—' : s.bot_count)),
				row(_('配置来源'), s.config_path || '—'),
				row(_('二维码来源'), s.log_source || '—'),
				row(_('二维码状态'), s.qr_available
					? _('有可扫的（约 %s 秒前生成）').format(s.qr_age == null ? '?' : s.qr_age)
					: (s.qr_stale ? _('只有旧的（已过期）') : _('没有'))),
				row(_('API 端口'), '%s%s'.format(s.api_port || '—',
					s.api_reachable ? _('（可达）') : _('（连不上）'))),
				row(_('推送地址'), s.notify_url || E('span', { 'class': 'pfw-note' }, _('未配置'))),
				row(_('最近一次推送'), s.last_push || E('span', { 'class': 'pfw-note' }, _('（无）')))
			])
		]);
	},

	/* ---------- 动作 ---------- */

	/* 统一的「跑一条 push 子命令 → 显示结果 → 刷新状态」流程 */
	runPush: function(title, args, opts) {
		var self = this;
		opts = opts || {};
		this.busy = true;
		return withBusy(title, pfExecJson(args)).then(function(res) {
			var ok = !!(res && res.ok);
			var msg = (res && (res.error || res.message)) || (ok ? _('完成。') : _('失败（没有更多信息）'));
			self.showResult(ok, msg, res);
			ui.addNotification(null, E('p', {}, (ok ? _('成功：') : _('失败：')) + msg), ok ? 'success' : 'danger');
			if (ok && opts.clearField)
				self.clearField(opts.clearField);
			return res;
		}).catch(function(err) {
			var msg = (err && err.message) ? err.message : String(err);
			self.showResult(false, msg);
			notifyError(err);
		}).then(function() {
			self.busy = false;
			return self.refresh(true).then(function() { self.schedule(IDLE_POLL); });
		});
	},

	handleSetServerchan: function(ev) {
		var el = document.getElementById('pfw-key');
		var key = (el && el.value ? el.value : '').trim();
		if (!key) {
			this.showResult(false, _('请先填 SendKey（在 sct.ftqq.com 微信扫码登录后获取）。' +
				'如果只是想重发一条测试消息，点上面的「发测试消息」。'));
			return;
		}
		return this.runPush(_('正在启用 Server酱 推送…'), [ 'push', 'set-serverchan', key ],
			{ clearField: 'pfw-key' });
	},

	handleSetWecom: function(ev) {
		var el = document.getElementById('pfw-wecom-url');
		var url = (el && el.value ? el.value : '').trim();
		if (!url) {
			this.showResult(false, _('请先粘贴企业微信群机器人的 Webhook 地址。'));
			return;
		}
		return this.runPush(_('正在启用企业微信推送…'), [ 'push', 'set-wecom', url ],
			{ clearField: 'pfw-wecom-url' });
	},

	handleSetWebhook: function(ev) {
		var val = function(id) {
			var el = document.getElementById(id);
			return el && el.value != null ? el.value.trim() : '';
		};
		var url = val('pfw-wh-url');
		if (!url) {
			this.showResult(false, _('请先填 webhook 地址。'));
			return;
		}
		var method = val('pfw-wh-method') || 'POST';
		var ctype = val('pfw-wh-ctype') || 'application/json';
		var body = document.getElementById('pfw-wh-body');
		body = body && body.value != null ? body.value : '';
		return this.runPush(_('正在启用自定义 webhook…'),
			[ 'push', 'set-webhook', url, method, ctype, body ]);
	},

	handleOff: function(ev) {
		return this.runPush(_('正在关闭推送…'), [ 'push', 'off' ]);
	},

	handleTest: function(ev) {
		return this.runPush(_('正在发送测试消息…'), [ 'push', 'test' ]);
	},

	handleRefresh: function(ev) {
		var self = this;
		this.busy = true;
		return withBusy(_('正在刷新…'), this.refresh(true)).then(function() {
			self.busy = false;
			self.schedule(IDLE_POLL);
		}, function(err) {
			self.busy = false;
			notifyError(err);
		});
	},

	/* ClawBot 向导的动作（沿用 1.0.2 的 wechat 子命令） */

	handleStart: function(ev) {
		var self = this;
		this.busy = true;
		return withBusy(_('正在启动微信服务…'), fs.exec(WECHAT_INIT, [ 'start' ])).then(function(res) {
			var out = ((res.stdout || '') + (res.stderr || '')).trim();
			if (res.code !== 0)
				throw new Error(out || _('退出码 %s').format(res.code));
			ui.addNotification(null, E('p', {}, _('微信服务已启动。')), 'success');
		}).catch(notifyError).then(function() {
			self.busy = false;
			return self.refresh(true).then(function() { self.schedule(WAIT_POLL); });
		});
	},

	handleRestart: function(ev) {
		var self = this;
		this.busy = true;
		return withBusy(_('正在重启微信服务…'), fs.exec(WECHAT_INIT, [ 'restart' ])).then(function(res) {
			var out = ((res.stdout || '') + (res.stderr || '')).trim();
			if (res.code !== 0)
				throw new Error(out || _('退出码 %s').format(res.code));
			ui.addNotification(null, E('p', {}, _('微信服务已重启，正在等它打印新二维码。')), 'success');
			self.qrAt = 0;
		}).catch(notifyError).then(function() {
			self.busy = false;
			return self.refresh(true).then(function() { self.schedule(WAIT_POLL); });
		});
	},

	handleQrRefresh: function(ev) {
		var self = this;
		this.qrAt = 0;
		return withBusy(_('正在刷新…'), this.refresh(true)).catch(notifyError);
	},

	handleEnable: function(ev) {
		var self = this;
		this.busy = true;
		return withBusy(_('正在启用微信推送…'), pfExecJson([ 'wechat', 'enable' ])).then(function(res) {
			if (res.ok)
				ui.addNotification(null, E('p', {}, _('微信推送已启用，测试消息已发送：%s').format(res.message || '')), 'success');
			else
				ui.addNotification(null, E('p', {}, _('启用失败：%s').format(res.error || res.message || _('原因见日志'))), 'danger');
		}).catch(notifyError).then(function() {
			self.busy = false;
			return self.refresh(true).then(function() { self.schedule(IDLE_POLL); });
		});
	}
});
