'use strict';
'require view';
'require form';
'require uci';
'require fs';
'require ui';
'require dom';

/*
 * 宿舍电量哨兵 · 设置页
 *
 * 直接绑定 /etc/config/powerfee 的几个 section：
 *   main（类型 powerfee）—— 查询间隔、阈值、提醒开关等；分三个页签：
 *                           监控与提醒 / 定时报告（report_time、report_days）/ 日志（log_file、log_max_kb）
 *   api（类型 api）      —— 查询接口；分三个页签：
 *                           接口地址（余额接口）/ 逐日用电量接口（daily_url、daily_body、
 *                           daily_method、daily_content_type、daily_timeout、daily_page_size）/
 *                           高级映射（daily_path、daily_ok_path、daily_ok_value、daily_msg_path、
 *                           daily_date_field、daily_used_field、daily_total_field、
 *                           daily_unit_field、unit_field）
 *   mail（类型 mail）    —— 默认发件账号（SMTP）与默认收件人
 *   notify（类型 notify）—— 默认推送通道（webhook）与查询端点
 *
 * 页面底部还有一块「邮箱账号（额外）」：管理 config mail_account 命名账号，
 * 给不同房间指定不同的发件邮箱（房间用 room.mail_account 引用；留空用上面的默认账号）。
 * 账号管理走命令行（powerfee json mail-accounts / powerfee mail-account …），
 * 不走 uci 表单：密钥（password）只在后端掩码，浏览器里没有明文可丢。
 *
 * 保存后由主程序在下一轮查询时自动重新读取配置，不需要重启服务。
 */

var PF_BIN = '/usr/bin/powerfee';

/* ---------- 命令行小工具（接口没落地 / 旧版本时只提示，不崩） ---------- */

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

/* 读一条 powerfee json 子命令；失败也返回 {error: '...'}，方便页面直接显示原因 */
function pfJsonSafe(args) {
	return fs.exec(PF_BIN, args).then(function(res) {
		var out = (res.stdout || '').trim();
		if (res.code !== 0)
			throw new Error(((res.stderr || '') + (res.stdout || '')).trim() ||
				_('powerfee 执行失败（退出码 %s）').format(res.code));
		if (!out)
			throw new Error(_('powerfee 没有输出'));
		return JSON.parse(out);
	}).catch(function(e) {
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

/* ---------- 账号列表的兜底解析（引擎侧字段命名变化时也不崩） ---------- */

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

/* json mail-accounts 的形状：accounts / list / 裸数组都认 */
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

/* 取第一个「是布尔」的字段（「是否已设置」这类标志） */
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
   （powerfee json mail-accounts 的形状：raw = 段里实际写的值，空 = 继承；
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

var PW_HINT_KEYS = [ 'password_hint', 'password_masked', 'secret_hint', 'password_mask', 'password' ];
var PW_SET_KEYS = [ 'password_set', 'has_password', 'password_configured', 'secret_set' ];

/* 密码状态：{ set: 是否已设置 | null 未知, hint: 掩码提示 } —— 真值永远不会回传 */
function pwState(a) {
	var set = acctBool(a, PW_SET_KEYS);
	var hint = firstVal(a, PW_HINT_KEYS);
	if (typeof hint === 'boolean') {
		if (set === null)
			set = hint;
		hint = '';
	}
	return { set: set, hint: String(hint || '') };
}

return view.extend({
	load: function() {
		/* 读不到也返回 {error:...}：页面显示提示，不崩 */
		return pfJsonSafe([ 'json', 'mail-accounts' ]);
	},

	render: function(data) {
		var m, s, o;

		/* undefined = 框架没调用 load()（理论上不会），buildAccounts 会自己取一次 */
		this.accountsData = data;

		m = new form.Map('powerfee', _('宿舍电量哨兵'),
			_('这里的改动保存后由主程序在下一轮查询时自动生效，<strong>不需要重启服务</strong>。' +
			  '配置写在 /etc/config/powerfee。'));

		/* ================= 监控（含定时报告 / 日志） ================= */

		s = m.section(form.NamedSection, 'main', 'powerfee', _('监控'));
		s.anonymous = true;
		s.addremove = false;

		s.tab('monitor', _('监控与提醒'),
			_('查询间隔、查询时段、阈值与提醒开关。'));
		s.tab('report', _('定时报告'),
			_('每天定点把「今日用量 + 最近 N 天曲线」发给每个启用的房间（邮件 + 推送，走房间自己的账号）。'));
		s.tab('log', _('日志'),
			_('运行日志写到哪里、文件多大就轮转。日志同时也会写 syslog（logread -e powerfee）。'));

		o = s.taboption('monitor', form.Flag, 'enabled', _('启用监控'),
			_('总开关。关闭后服务仍会运行，但不再自动查询、也不发任何邮件（命令行查询仍可用）。'));
		o.default = '1';
		o.rmempty = false;

		o = s.taboption('monitor', form.Value, 'interval', _('查询间隔（秒）'),
			_('多久查一次余额，默认 1800（30 分钟）。学校接口一次要返回全校数据，' +
			  '间隔太小会白耗流量，不建议低于 600。'));
		o.datatype = 'uinteger';
		o.placeholder = '1800';

		o = s.taboption('monitor', form.Value, 'retry_interval', _('失败重试间隔（秒）'),
			_('查询失败后隔多久重试，默认 300（5 分钟）。'));
		o.datatype = 'uinteger';
		o.placeholder = '300';

		o = s.taboption('monitor', form.Value, 'active_hours', _('查询时段'),
			_('只在指定时段内查询接口，减少夜间请求。<strong>留空 = 全天查询</strong>（默认，与旧版行为一致）。' +
			  '格式 <code>HH:MM-HH:MM</code>，多段用空格分隔：<code>07:00-12:00 14:00-23:00</code>；' +
			  '起止时间反过来表示跨越午夜：<code>22:00-06:00</code>（22 点到次日 6 点）；' +
			  '起止相同表示全天。时段是「含起点、不含终点」。' +
			  '窗口外不发任何请求，窗口外的时长也不计入「监控失效」判定，因此不会因为夜里没查而误报。' +
			  '需要立刻查一次时用 <code>powerfee check --force</code>（或状态页的「立即查询」在窗口外会被跳过）。'));
		o.placeholder = '07:00-23:00';

		o = s.taboption('monitor', form.Value, 'threshold', _('不足阈值（度）'),
			_('余额低于这个值就算「电费不足」（红色档），默认 20。留空表示用默认值。'));
		o.datatype = 'ufloat';
		o.placeholder = '20';

		o = s.taboption('monitor', form.Value, 'warn_ratio', _('预警倍数'),
			_('余额低于「不足阈值 × 这个倍数」时进入预警（橙色档），默认 2，即低于 40 度预警。'));
		o.datatype = 'ufloat';
		o.placeholder = '2';

		o = s.taboption('monitor', form.Value, 'cooldown', _('重复提醒冷却（分钟）'),
			_('同一档位（例如一直是低电量）重复发邮件的最小间隔，默认 180 分钟。'));
		o.datatype = 'uinteger';
		o.placeholder = '180';

		o = s.taboption('monitor', form.Flag, 'notify_warn', _('预警时也发邮件'),
			_('勾选后，进入预警区（橙色档）也会发一封提醒；不勾则只在「电费不足」时发。'));
		o.default = '1';
		o.rmempty = false;

		o = s.taboption('monitor', form.Flag, 'notify_recovery', _('恢复时发邮件'),
			_('从「电费不足」回到阈值以上（充值成功）时，发一封「已恢复」的邮件。'));
		o.default = '1';
		o.rmempty = false;

		o = s.taboption('monitor', form.Flag, 'notify_error', _('监控失效时发邮件'),
			_('连续取不到数据超过下面的小时数时，发一封「监控异常」的邮件。'));
		o.default = '1';
		o.rmempty = false;

		o = s.taboption('monitor', form.Value, 'stale_hours', _('监控失效判定（小时）'),
			_('连续多少小时取不到数据算「监控失效」，默认 6。'));
		o.datatype = 'uinteger';
		o.placeholder = '6';

		/* ---- 定时报告（main.report_time / main.report_days）---- */

		o = s.taboption('report', form.Value, 'report_time', _('报告时间（HH:MM）'),
			_('每天几点发一份「今日用量 + 最近 N 天曲线」的报告，例如 21:00。' +
			  '<strong>留空 = 不发</strong>（默认）。到点给每个启用的房间各发一份：' +
			  '邮件带图、推送带单行摘要，都用房间自己的收件人 / 发件账号 / 推送账号。' +
			  '报告时间不受「查询时段」限制（曲线来自逐日接口，与查询无关）。'));
		o.placeholder = '21:00';

		o = s.taboption('report', form.Value, 'report_days', _('报告曲线天数'),
			_('报告（以及告警邮件）里曲线的天数，默认 7，可填 1~92。' +
			  '手动发报告时 <code>powerfee report --days N</code> 的 N 也用它当默认值。'));
		o.datatype = 'range(1,92)';
		o.default = '7';
		o.placeholder = '7';

		/* ---- 日志（main.log_file / main.log_max_kb）---- */

		o = s.taboption('log', form.Value, 'log_file', _('日志文件'),
			_('运行日志写到哪个文件，默认 <code>/etc/powerfee/powerfee.log</code>。' +
			  '<strong>留空 = 不写文件</strong>（仍然写 syslog，可用 logread -e powerfee 查看）。'));
		o.placeholder = '/etc/powerfee/powerfee.log';

		o = s.taboption('log', form.Value, 'log_max_kb', _('日志文件上限（KB）'),
			_('日志文件超过这个大小就改名成 <code>.old</code>（旧的 .old 被覆盖），默认 128 KB。' +
			  '<strong>填 0 = 不轮转</strong>（文件会一直变大，不建议）。'));
		o.datatype = 'uinteger';
		o.placeholder = '128';

		/* ================= 查询接口 ================= */

		s = m.section(form.NamedSection, 'api', 'api', _('查询接口'),
			_('换成你所在学校的电费查询接口时，改这一组就行——主程序按这里的配置抓取并解析数据。' +
			  '「逐日用电量接口」是可选的：配了才有每日曲线与报告，不配则退回本地采样估算。'));
		s.anonymous = true;
		s.addremove = false;

		s.tab('basic', _('接口地址'),
			_('抓取余额（房间列表）的学校接口。'));
		s.tab('daily', _('逐日用电量接口'),
			_('可选：填了「逐日接口地址」才有逐日用电量曲线与报告，不再依赖本地采样估算。'));
		s.tab('adv', _('高级映射'),
			_('返回结构的路径 / 字段名映射。一般不用改；换学校接口且默认值对不上时才需要。'));

		o = s.taboption('basic', form.Value, 'url', _('接口地址'),
			_('你学校电费查询接口的完整 URL（含查询参数）。用浏览器开发者工具抓一次查询请求，' +
			  '把请求的完整地址复制过来即可。留空表示还没配置，主程序会提示「尚未配置学校接口」。'));
		o.placeholder = 'https://example.edu.cn/api/powerfee?from=app';

		o = s.taboption('basic', form.ListValue, 'method', _('请求方法'),
			_('接口用 POST 还是 GET。抓包时看到参数在 URL 里就选 GET，参数在请求体里就选 POST（默认）。'));
		o.value('POST', 'POST');
		o.value('GET', 'GET');
		o.default = 'POST';

		o = s.taboption('basic', form.Value, 'content_type', _('Content-Type（POST）'),
			_('POST 时的请求头 Content-Type，默认 application/x-www-form-urlencoded。'));
		o.placeholder = 'application/x-www-form-urlencoded';
		o.default = 'application/x-www-form-urlencoded';
		o.depends('method', 'POST');

		o = s.taboption('basic', form.Value, 'body', _('请求体（POST）'),
			_('POST 时发送的内容，形如 a=1&b=2（抓包时看到的那串参数）。GET 时忽略。'));
		o.depends('method', 'POST');

		o = s.taboption('basic', form.Value, 'timeout', _('请求超时（秒）'),
			_('单次请求接口的超时时间，默认 40。校园网慢或接口返回数据大时可以调大。'));
		o.datatype = 'uinteger';
		o.placeholder = '40';
		o.default = '40';

		o = s.taboption('basic', form.Value, 'dns_servers', _('备用 DNS 服务器'),
			_('路由器本机解析不了接口域名时，用这些 DNS 解析出 IP 后直连。多个用空格分隔，留空则只用系统 DNS。'));
		o.placeholder = '223.5.5.5 119.29.29.29';
		o.default = '223.5.5.5 119.29.29.29';

		o = s.taboption('basic', form.Value, 'rooms_path', _('房间数组路径'),
			_('jsonfilter 表达式，指向返回 JSON 里「房间对象数组」的位置。' +
			  '默认 @.obj[*] 对应 {"obj":[...]}；若返回是 {"data":{"list":[...]}} 就填 @.data.list[*]。'));
		o.placeholder = '@.obj[*]';
		o.default = '@.obj[*]';

		o = s.taboption('basic', form.Value, 'ok_path', _('成功标志字段路径'),
			_('可选。jsonfilter 表达式，指向表示「查询成功」的字段，例如 @.code；留空表示不检查。'));
		o.placeholder = '@.code';

		o = s.taboption('basic', form.Value, 'ok_value', _('成功标志期望值'),
			_('可选。上面那个字段等于这个值才算成功，例如 true 或 0。ok_path 留空时这里也没用。'));
		o.placeholder = 'true';

		o = s.taboption('basic', form.Value, 'msg_path', _('错误信息字段路径'),
			_('可选。jsonfilter 表达式，指向返回 JSON 里的错误描述字段（例如 @.msg），查询失败时会写进日志。'));
		o.placeholder = '@.msg';

		o = s.taboption('basic', form.Value, 'field_id', _('房间编号字段名'),
			_('房间对象里「唯一编号」的字段名，例如 roomNum、id。保存房间时优先按它精确匹配。'));
		o.placeholder = 'roomNum';
		o.default = 'roomNum';

		o = s.taboption('basic', form.Value, 'field_name', _('房间号字段名'),
			_('房间对象里「房间号（门牌号）」的字段名，例如 room。搜索、显示、保存都用它。'));
		o.placeholder = 'room';
		o.default = 'room';

		o = s.taboption('basic', form.Value, 'field_building', _('楼栋字段名'),
			_('房间对象里「楼栋」的字段名，例如 building。'));
		o.placeholder = 'building';
		o.default = 'building';

		o = s.taboption('basic', form.Value, 'field_campus', _('校区字段名'),
			_('房间对象里「校区」的字段名，例如 schoolArea。没有校区概念可以留空。'));
		o.placeholder = 'schoolArea';
		o.default = 'schoolArea';

		o = s.taboption('basic', form.Value, 'field_balance', _('余额字段名'),
			_('房间对象里「余额」的字段名，例如 powerBalance。'));
		o.placeholder = 'powerBalance';
		o.default = 'powerBalance';

		o = s.taboption('basic', form.Value, 'unit', _('电量单位'),
			_('界面上显示用的单位，默认「度」。有些学校按金额计费可以填「元」。'));
		o.placeholder = '度';
		o.default = '度';

		/* ---- 逐日用电量接口（api.daily_*，1.2.0）---- */

		o = s.taboption('daily', form.Value, 'daily_url', _('逐日接口地址'),
			_('你学校「每日用电明细」接口的完整 URL。抓一次「查询每日用电」请求，把地址复制过来。' +
			  '<strong>留空 = 不启用逐日接口</strong>（曲线与报告自动退回本地采样估算，仅供参考）。'));
		o.placeholder = 'https://你的学校/user/powerfee/getDailyDetails';

		o = s.taboption('daily', form.Value, 'daily_body', _('请求体模板'),
			_('请求参数模板（POST 时作请求体，GET 时拼到地址后面）。四个占位符会被替换：' +
			  '<code>{room}</code> 房间编号、<code>{month}</code> 月份（YYYY-MM）、' +
			  '<code>{page}</code> 页码（目前固定第 1 页）、<code>{page_size}</code> 每页天数（取下面的「每页天数」）。' +
			  '留空 = 用默认模板。'));
		o.placeholder = 'roomNum={room}&lastDate={month}&type=2&pageNum={page}&pageSize={page_size}';

		o = s.taboption('daily', form.ListValue, 'daily_method', _('请求方法'),
			_('逐日接口用 POST 还是 GET，默认 POST（参数在请求体里）。抓包看到参数都在 URL 里就选 GET。'));
		o.value('POST', 'POST');
		o.value('GET', 'GET');
		o.default = 'POST';

		o = s.taboption('daily', form.Value, 'daily_content_type', _('Content-Type（POST）'),
			_('POST 时的请求头 Content-Type，默认 application/x-www-form-urlencoded。'));
		o.placeholder = 'application/x-www-form-urlencoded';
		o.default = 'application/x-www-form-urlencoded';
		o.depends('daily_method', 'POST');

		o = s.taboption('daily', form.Value, 'daily_timeout', _('请求超时（秒）'),
			_('单次请求逐日接口的超时时间，默认 30。余额告警邮件里现画曲线时最多等 10 秒，不会拖慢发信。'));
		o.datatype = 'uinteger';
		o.placeholder = '30';
		o.default = '30';

		o = s.taboption('daily', form.Value, 'daily_page_size', _('每页天数'),
			_('模板里 <code>{page_size}</code> 的取值，默认 31（一个月最多 31 天，一页就够，不用翻页）。'));
		o.datatype = 'uinteger';
		o.placeholder = '31';
		o.default = '31';

		/* ---- 高级映射（一般不用改）---- */

		o = s.taboption('adv', form.Value, 'daily_path', _('逐日明细数组路径'),
			_('jsonfilter 表达式，指向返回 JSON 里「每天一条记录」的数组。' +
			  '默认 @.obj.dailyDetailsInfos[*] 对应 {"obj":{"dailyDetailsInfos":[...]}}。'));
		o.placeholder = '@.obj.dailyDetailsInfos[*]';
		o.default = '@.obj.dailyDetailsInfos[*]';

		o = s.taboption('adv', form.Value, 'daily_ok_path', _('逐日成功标志路径'),
			_('可选。jsonfilter 表达式，指向表示「逐日查询成功」的字段，例如 @.ret；留空 = 不检查。'));
		o.placeholder = '@.ret';

		o = s.taboption('adv', form.Value, 'daily_ok_value', _('逐日成功标志期望值'),
			_('可选。上面那个字段等于这个值才算成功，例如 true 或 0。daily_ok_path 留空时这里也没用。'));
		o.placeholder = 'true';

		o = s.taboption('adv', form.Value, 'daily_msg_path', _('逐日错误信息路径'),
			_('可选。指向返回 JSON 里的错误描述字段（例如 @.msg），逐日查询失败时会写进日志。'));
		o.placeholder = '@.msg';

		o = s.taboption('adv', form.Value, 'daily_date_field', _('逐日日期字段名'),
			_('每条记录里「日期」的字段名（格式 YYYY-MM-DD），默认 dateTime。'));
		o.placeholder = 'dateTime';
		o.default = 'dateTime';

		o = s.taboption('adv', form.Value, 'daily_used_field', _('当日用量字段名'),
			_('每条记录里「当天用电量」的字段名，默认 dailyUsed。'));
		o.placeholder = 'dailyUsed';
		o.default = 'dailyUsed';

		o = s.taboption('adv', form.Value, 'daily_total_field', _('终身累计字段名'),
			_('每条记录里「终身累计用电量」（单调递增）的字段名，默认 totalUsed。' +
			  '跨月补齐「历史月最后一天」的数据时要用它，填错会让那天的值算不出来。'));
		o.placeholder = 'totalUsed';
		o.default = 'totalUsed';

		o = s.taboption('adv', form.Value, 'daily_unit_field', _('逐日单位字段名'),
			_('逐日数据里「单位」的字段名，默认 dailyUsedUnit；拿不到就用上面的「电量单位」。留空 = 只用「电量单位」。'));
		o.placeholder = 'dailyUsedUnit';
		o.default = 'dailyUsedUnit';

		o = s.taboption('adv', form.Value, 'unit_field', _('余额单位字段名'),
			_('余额接口返回里「单位」的字段名（例如 du 恒为「度」），默认 du：接口返回有就优先用它，' +
			  '拿不到再回落到上面的「电量单位」。留空 = 只用「电量单位」。'));
		o.placeholder = 'du';
		o.default = 'du';

		/* ================= 邮件（默认发件账号） ================= */

		s = m.section(form.NamedSection, 'mail', 'mail', _('邮件提醒（默认发件账号）'),
			_('这一组就是<strong>默认发件账号</strong>（<code>mail</code> 段）：没有单独指定发件账号的房间都用它。' +
			  '想给不同房间用不同的发件邮箱，到页面底部的「邮箱账号（额外）」里添加命名账号，' +
			  '再到「宿舍管理」页按房间指定。'));
		s.anonymous = true;
		s.addremove = false;

		o = s.option(form.Flag, 'enabled', _('启用邮件提醒'),
			_('不勾选时不会发任何邮件（包括测试邮件）。'));
		o.default = '0';
		o.rmempty = false;

		o = s.option(form.ListValue, 'transport', _('投递方式'),
			_('用哪个程序发信。auto = 有 python3 就用 python3，否则用 msmtp。'));
		o.value('auto', _('自动（推荐）'));
		o.value('python3', _('python3（内置 SMTP 库）'));
		o.value('msmtp', _('msmtp 命令'));
		o.default = 'auto';

		o = s.option(form.Value, 'host', _('SMTP 服务器'),
			_('例如 QQ 邮箱 smtp.qq.com、163 邮箱 smtp.163.com、Gmail smtp.gmail.com。'));
		o.placeholder = 'smtp.qq.com';

		o = s.option(form.Value, 'port', _('SMTP 端口'),
			_('SSL 一般 465，STARTTLS 一般 587。'));
		o.datatype = 'port';
		o.placeholder = '465';

		o = s.option(form.ListValue, 'security', _('加密方式'),
			_('SSL = 直接加密连接（465 端口）；STARTTLS = 先明文再升级（587 端口）。'));
		o.value('ssl', _('SSL'));
		o.value('starttls', _('STARTTLS'));
		o.value('none', _('不加密'));
		o.default = 'ssl';

		o = s.option(form.Value, 'user', _('邮箱账号'),
			_('登录 SMTP 的用户名，一般是完整邮箱地址。'));
		o.placeholder = 'you@qq.com';

		o = s.option(form.Value, 'password', _('邮箱密码 / 授权码'),
			_('QQ、163 等邮箱这里要填「SMTP 授权码」，不是登录密码。'));
		o.password = true;

		o = s.option(form.Value, 'from', _('发件人地址'),
			_('留空则用上面的邮箱账号。多数邮箱要求发件人与账号一致。'));
		o.placeholder = _('留空则用邮箱账号');

		o = s.option(form.Value, 'from_name', _('发件人显示名'),
			_('收件人看到的发件人名字，默认「宿舍电量哨兵」。'));
		o.placeholder = '宿舍电量哨兵';

		o = s.option(form.Value, 'to', _('收件人'),
			_('提醒邮件的收件地址，多个用英文逗号分隔。'));
		o.placeholder = 'you@example.com';

		o = s.option(form.Flag, 'tls_verify', _('校验服务器证书'),
			_('默认开启。自建/企业邮箱用自签证书、报证书错误时可以关掉（不推荐）。'));
		o.default = '1';
		o.rmempty = false;

		/* ================= 通知推送 ================= */

		s = m.section(form.NamedSection, 'notify', 'notify', _('通知推送'),
			_('除邮件之外的第二条通知通道（HTTP webhook）。两条通道互相独立：' +
			  '可以只开这一条、把邮件关掉（mail.enabled=0 + notify.enabled=1），也可以两条都开。'));
		s.anonymous = true;
		s.addremove = false;

		o = s.option(form.Flag, 'enabled', _('启用通知推送'),
			_('打开后，余额不足 / 充值恢复 / 监控失效等事件会往下面的地址推一条消息。' +
			  '不勾选则完全不推送（邮件通道不受影响）。'));
		o.default = '0';
		o.rmempty = false;

		o = s.option(form.Value, 'url', _('推送地址（webhook）'),
			_('接收推送的完整 HTTP(S) 地址，例如企业微信 / 钉钉 / 飞书机器人、Bark、自建服务等。' +
			  '留空表示还没配置：此时就算打开开关也不会推送，日志里会提示「未配置 notify.url」。'));
		o.placeholder = 'https://example.com/webhook';

		o = s.option(form.ListValue, 'method', _('请求方法'),
			_('推送用 POST 还是 GET，默认 POST。多数机器人 webhook 用 POST。'));
		o.value('POST', 'POST');
		o.value('GET', 'GET');
		o.default = 'POST';

		o = s.option(form.Value, 'content_type', _('Content-Type（POST）'),
			_('POST 时的请求头 Content-Type，默认 application/json。'));
		o.placeholder = 'application/json';
		o.default = 'application/json';
		o.depends('method', 'POST');

		o = s.option(form.Value, 'body', _('请求体模板'),
			_('推送内容模板，<code>{xxx}</code> 会被替换成实际值（值里的引号会自动转义，未知占位符原样保留）。' +
			  '默认 <code>{"text":"{text}"}</code> 适配大多数机器人。可用占位符：' +
			  '<code>{text}</code> 单行摘要（给聊天窗口用，本身不含引号换行，可直接嵌进 JSON 字符串）、' +
			  '<code>{title}</code> 标题、<code>{room}</code> 房间、<code>{balance}</code> 余额、' +
			  '<code>{unit}</code> 单位、<code>{level}</code> 档位、<code>{reason}</code> 触发原因、' +
			  '<code>{daily}</code> 日均用量、<code>{days_left}</code> 预计可用天数、' +
			  '<code>{time}</code> 时间、<code>{device}</code> 路由器名。' +
			  '选 GET 时，模板会做 URL 编码后作为查询串拼到地址后面。'));
		o.placeholder = '{"text":"{text}"}';
		o.default = '{"text":"{text}"}';

		o = s.option(form.Value, 'token', _('鉴权令牌'),
			_('可选。推送地址需要鉴权时填这里，会按下一项指定的请求头发送；留空则不发送鉴权头。'));
		o.placeholder = _('留空则不发送');

		o = s.option(form.Value, 'token_header', _('鉴权请求头'),
			_('令牌放在哪个请求头里，默认 Authorization；有些服务要 X-Api-Key、X-Token 之类。'));
		o.placeholder = 'Authorization';
		o.default = 'Authorization';

		o = s.option(form.Value, 'timeout', _('推送超时（秒）'),
			_('单次推送请求的超时时间，默认 15。'));
		o.datatype = 'uinteger';
		o.placeholder = '15';
		o.default = '15';

		o = s.option(form.Flag, 'http_enabled', _('开启查询端点'),
			_('给微信 / QQ 机器人用的只读查询接口。开启后，局域网内可以请求：' +
			  '<br><code>https://&lt;路由器IP&gt;:8443/cgi-bin/powerfee?token=&lt;查询令牌&gt;&amp;cmd=brief</code>' +
			  '<br>取当前状态（cmd 可选 status / brief / groups / rooms / history / log / check）。' +
			  '<strong>必须设置下面的「查询令牌」</strong>，否则端点一律返回 403；' +
			  '端点只对局域网内网地址开放，不要暴露到公网。'));
		o.default = '0';
		o.rmempty = false;

		o = s.option(form.Value, 'http_token', _('查询令牌'),
			_('查询端点要求的 token：必须设置，且建议用足够长的随机串；留空等于端点关闭。' +
			  '机器人请求时把它填进 token= 参数，注意别泄露给别人。'));
		o.placeholder = _('如 32 位随机字符串');

		/* ================= 邮箱账号（额外） ================= */

		var accountsNode = this.buildAccounts();

		return m.render().then(function(node) {
			return E('div', {}, [ node, accountsNode ]);
		});
	},

	/* ---------- 邮箱账号（额外）：列表 ---------- */

	buildAccounts: function() {
		this.acctNode = E('div', { 'class': 'cbi-section' });
		this.paintAccounts();
		if (this.accountsData === undefined)
			this.refreshAccounts();     /* 框架没调用 load() 时的兜底：自己取一次 */
		return this.acctNode;
	},

	paintAccounts: function() {
		var self = this;
		var res = this.accountsData;

		if (res === undefined) {
			dom.content(this.acctNode, [
				E('h3', {}, _('邮箱账号（额外）')),
				E('p', {}, E('em', {}, _('读取中…')))
			]);
			return;
		}

		res = res || {};
		var list = accountList(res);
		var nodes = [];

		nodes.push(E('h3', {}, _('邮箱账号（额外）')));
		nodes.push(E('p', { 'class': 'cbi-value-description' },
			_('上面「邮件提醒」一组就是<strong>默认发件账号</strong>（<code>mail</code> 段），没单独指定账号的房间都用它。' +
			  '这里可以再添加若干命名账号（<code>config mail_account</code>），' +
			  '然后在「宿舍管理」页给每个房间选一个发件账号（房间的 <code>mail_account</code> 引用）——' +
			  '比如不同楼栋用不同的发件邮箱。房间留空引用时仍然用默认账号。')));

		if (!list) {
			nodes.push(E('div', { 'class': 'alert-message warning' }, [
				E('h4', {}, _('读不到邮箱账号列表')),
				E('p', {}, String(res.error || _('powerfee 没有返回账号列表。'))),
				E('p', {}, _('多半是 powerfee 还是旧版本（没有 mail-account 子命令）；升级到 1.2.0 后这里就能管理多个发件账号。'))
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
			}, _('添加邮箱账号')),
			' ',
			E('button', {
				'class': 'cbi-button',
				'click': ui.createHandlerFn(this, 'handleAccountsRefresh')
			}, _('刷新列表'))
		]));

		/* 引擎回的账号问题清单（缺 host / 停用 / 引用了不存在的账号等） */
		var issues = Array.isArray(res.issues) ? res.issues.filter(function(it) {
			return it && it.kind !== 'push' && it.kind !== 'push_account';
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
			nodes.push(E('p', {}, E('em', {}, _('还没有额外账号，所有房间都用上面的默认账号。'))));
			dom.content(this.acctNode, nodes);
			return;
		}

		var rows = [ E('tr', { 'class': 'tr table-titles' }, [
			E('th', {}, _('名称')),
			E('th', {}, _('状态')),
			E('th', {}, _('SMTP 服务器（实际生效）')),
			E('th', {}, _('登录账号')),
			E('th', {}, _('发件人')),
			E('th', {}, _('密码')),
			E('th', { 'class': 'right' }, _('操作'))
		]) ];

		list.forEach(function(a) {
			var name = accName(a);
			var host = acctEff(a, [ 'host' ]) || acctRaw(a, [ 'host' ]);
			var port = acctEff(a, [ 'port' ]) || acctRaw(a, [ 'port' ]);
			var sec = acctEff(a, [ 'security' ]) || acctRaw(a, [ 'security' ]);
			var userHint = acctEff(a, [ 'user_hint' ]);
			var userSet = acctBool(a, [ 'user_set' ]);
			var sender = acctEff(a, [ 'sender' ]) || acctEff(a, [ 'from' ]) || acctRaw(a, [ 'from' ]);
			var pw = pwState(a);
			var usable = firstBool(a, [ 'usable' ]);
			var issue = firstVal(a, [ 'issue' ]);
			var usedBy = (a && Array.isArray(a.used_by)) ? a.used_by : null;

			rows.push(E('tr', { 'class': 'tr' }, [
				E('td', {}, [
					E('strong', {}, name || _('（未命名）')),
					usedBy
						? E('div', { 'class': 'cbi-value-description' },
							usedBy.length ? _('用于：%s').format(usedBy.join('、')) : _('（没有房间在用）'))
						: ''
				]),
				E('td', {}, [
					accDisabled(a)
						? E('span', { 'style': 'color:#d93025' }, _('已停用'))
						: (usable === false ? _('启用（不可用）') : _('启用')),
					(issue && usable === false)
						? E('div', { 'class': 'cbi-value-description' }, String(issue))
						: ''
				]),
				E('td', {}, host
					? [
						'%s%s'.format(host, port ? ':' + port : '') + (sec ? '（%s）'.format(sec) : ''),
						acctRaw(a, [ 'host' ])
							? ''
							: E('div', { 'class': 'cbi-value-description' }, _('继承 mail 段'))
					]
					: E('span', { 'class': 'cbi-value-description' }, _('继承默认'))),
				E('td', {}, userHint ? String(userHint)
					: (userSet ? _('已设置') : E('span', { 'class': 'cbi-value-description' }, _('继承默认')))),
				E('td', {}, sender ? String(sender)
					: E('span', { 'class': 'cbi-value-description' }, _('留空用登录账号'))),
				E('td', {}, pw.hint ? String(pw.hint)
					: (pw.set ? _('已设置')
						: E('span', { 'class': 'cbi-value-description' }, _('未设置')))),
				E('td', { 'class': 'right', 'style': 'white-space:nowrap' }, [
					E('button', {
						'class': 'cbi-button cbi-button-action',
						'click': ui.createHandlerFn(self, 'handleAccountEdit', a)
					}, _('编辑')),
					' ',
					E('button', {
						'class': 'cbi-button',
						'click': ui.createHandlerFn(self, 'handleAccountTest', a)
					}, _('发测试邮件')),
					' ',
					E('button', {
						'class': 'cbi-button cbi-button-remove',
						'click': ui.createHandlerFn(self, 'handleAccountRemove', a)
					}, _('删除'))
				])
			]));
		});

		nodes.push(E('table', { 'class': 'table' }, rows));
		nodes.push(E('p', { 'class': 'cbi-value-description' },
			_('密钥（password）只显示「已设置」提示，不会回显真值；编辑时留空表示不修改。' +
			  '每个账号里留空的字段 = 继承 <code>mail</code> 段的同名字段（enabled 除外，默认启用）。')));

		dom.content(this.acctNode, nodes);
	},

	refreshAccounts: function() {
		var self = this;
		return pfJsonSafe([ 'json', 'mail-accounts' ]).then(function(res) {
			self.accountsData = res;
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
		return withBusy(_('正在发送测试邮件…'), pfRun([ 'mail-account', 'test', name ])).then(function(res) {
			var ok = !(res && res.ok === false);
			var msg = (res && (res.message || res.error)) || _('测试邮件已发送。');
			ui.addNotification(null, E('p', {}, (ok ? _('成功：') : _('失败：')) + msg), ok ? 'success' : 'danger');
		}).catch(notifyError);
	},

	handleAccountRemove: function() {
		var account = handlerArgs(arguments)[0];
		var self = this;
		var name = accName(account);

		ui.showModal(_('删除邮箱账号'), [
			E('p', {}, _('确定删除邮箱账号「%s」吗？').format(name)),
			E('p', { 'class': 'cbi-value-description' },
				_('引用它的房间会自动回到默认账号（mail 段），不会影响别的账号。')),
			E('div', { 'class': 'right' }, [
				E('button', { 'class': 'btn cbi-button cbi-button-neutral', 'click': ui.hideModal }, _('取消')),
				' ',
				E('button', {
					'class': 'btn cbi-button cbi-button-remove',
					'click': function(ev) {
						ui.hideModal();
						withBusy(_('正在删除…'), pfRun([ 'mail-account', 'remove', name ])).then(function(res) {
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

	/* ---------- 邮箱账号（额外）：新增 / 编辑弹窗 ---------- */

	showAccountModal: function(account) {
		var self = this;
		var a = account || {};
		var isNew = !account;
		var fields = {};
		var prefill = {};
		var pw = pwState(a);
		var userSet = acctBool(a, [ 'user_set' ]);
		var userHint = acctEff(a, [ 'user_hint' ]);

		function fieldRow(key, label, opts) {
			opts = opts || {};
			/* 密钥字段永远不回显（引擎只回掩码，也不能把掩码当值再存回去）；
			   其余字段回显「账号自己写的那份」（raw，空 = 继承） */
			var v = opts.secret ? '' : acctRaw(a, [ key ]);
			prefill[key] = (v === '' ? '' : String(v));
			var input = E('input', {
				'type': opts.secret ? 'password' : 'text',
				'class': 'cbi-input-text',
				'value': prefill[key],
				'placeholder': opts.placeholder || ''
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
			return E('div', { 'class': 'cbi-value' }, [
				E('label', { 'class': 'cbi-value-title' }, label),
				E('div', { 'class': 'cbi-value-field' }, [
					E('div', {}, line),
					opts.hint ? E('div', { 'class': 'cbi-value-description' }, opts.hint) : ''
				])
			]);
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
			return E('div', { 'class': 'cbi-value' }, [
				E('label', { 'class': 'cbi-value-title' }, label),
				E('div', { 'class': 'cbi-value-field' }, [
					sel,
					hint ? E('div', { 'class': 'cbi-value-description' }, hint) : ''
				])
			]);
		}

		/* 段名：新建时可填；编辑时固定（引擎没有改名接口，要改就删了重建） */
		var nameInput = null;
		var nameNode;
		if (isNew) {
			nameInput = E('input', { 'type': 'text', 'class': 'cbi-input-text', 'placeholder': 'qq_main' });
			nameNode = nameInput;
		}
		else {
			nameNode = E('strong', {}, accName(a) || _('（未命名）'));
		}

		prefill.enabled = accDisabled(a) ? '0' : '1';
		fields.enabled = E('input', { 'type': 'checkbox' });
		if (prefill.enabled === '1')
			fields.enabled.checked = true;

		var clearPw = E('input', { 'type': 'checkbox' });

		/* 只把「用户改过的字段」发出去：列表里没回的字段不会因此被清空 */
		function collect() {
			var kvs = [];
			Object.keys(fields).forEach(function(key) {
				if (key === 'password')
					return;                 /* 单独处理 */
				var el = fields[key];
				var v = (el.type === 'checkbox') ? (el.checked ? '1' : '0') : String(el.value || '').trim();
				if (isNew || v !== prefill[key])
					kvs.push([ key, v ]);
			});
			var pwv = String(fields.password.value || '');
			if (pwv)
				kvs.push([ 'password', pwv ]);
			else if (clearPw.checked)
				kvs.push([ 'password', '' ]);
			return kvs;
		}

		function submit(ev, andTest) {
			var name = nameInput ? String(nameInput.value || '').trim() : accName(a);
			if (!name) {
				ui.addNotification(null, E('p', {}, _('请先给账号起个名字（段名），例如 qq_main。')), 'warning');
				return;
			}
			if (!/^[A-Za-z0-9_]+$/.test(name)) {
				ui.addNotification(null, E('p', {}, _('名字只能用字母、数字和下划线。')), 'warning');
				return;
			}
			ui.hideModal();
			return self.saveAccount(name, isNew, collect(), andTest);
		}

		ui.showModal(isNew ? _('添加邮箱账号') : _('编辑邮箱账号：%s').format(accName(a)), [
			E('div', { 'class': 'cbi-map' }, [
				E('div', { 'class': 'cbi-value' }, [
					E('label', { 'class': 'cbi-value-title' }, _('账号名（段名）')),
					E('div', { 'class': 'cbi-value-field' }, [
						nameNode,
						E('div', { 'class': 'cbi-value-description' },
							_('只用字母、数字、下划线，例如 qq_main；房间在「宿舍管理」页按这个名字引用。'))
					])
				]),
				E('div', { 'class': 'cbi-value' }, [
					E('label', { 'class': 'cbi-value-title' }, _('启用')),
					E('div', { 'class': 'cbi-value-field' },
						E('label', {}, [ fields.enabled, ' ', _('启用这个账号') ]))
				]),
				selectRow('transport', _('投递方式'), [
					[ '', _('继承默认（mail.transport）') ],
					[ 'auto', _('自动（推荐）') ],
					[ 'python3', _('python3（内置 SMTP 库）') ],
					[ 'msmtp', _('msmtp 命令') ]
				]),
				fieldRow('host', _('SMTP 服务器'), { placeholder: 'smtp.example.com', hint: _('留空继承 mail.host。') }),
				fieldRow('port', _('SMTP 端口'), { placeholder: '465', hint: _('留空继承 mail.port。') }),
				selectRow('security', _('加密方式'), [
					[ '', _('继承默认（mail.security）') ],
					[ 'ssl', 'SSL' ],
					[ 'starttls', 'STARTTLS' ],
					[ 'none', _('不加密') ]
				]),
				fieldRow('user', _('登录账号'), {
					placeholder: userSet ? _('已设置（填新的会覆盖）') : _('you@example.com'),
					hint: userHint ? _('已保存：%s（不会回显；留空 = 不修改）').format(userHint)
						: (userSet ? _('已保存一个登录账号（不会回显）；留空 = 不修改。')
							: _('留空继承 mail.user。'))
				}),
				fieldRow('password', _('密码 / 授权码'), {
					secret: true,
					placeholder: pw.set ? _('已设置（填新的会覆盖）') : _('留空 = 不修改'),
					hint: pw.hint ? _('已保存：%s').format(pw.hint)
						: (pw.set ? _('已保存一个密码（不会回显）。')
							: _('还没设置密码。QQ / 163 等邮箱填的是「SMTP 授权码」。'))
				}),
				E('div', { 'class': 'cbi-value' }, [
					E('label', { 'class': 'cbi-value-title' }, ''),
					E('div', { 'class': 'cbi-value-field' },
						E('label', {}, [ clearPw, ' ', _('清除已保存的密码（改回继承 mail.password）') ]))
				]),
				fieldRow('from', _('发件人地址'), { placeholder: 'you@example.com', hint: _('留空继承 mail.from；仍然留空则用登录账号。') }),
				fieldRow('from_name', _('发件人显示名'), { placeholder: '宿舍电量哨兵' }),
				selectRow('tls_verify', _('校验服务器证书'), [
					[ '', _('继承默认（mail.tls_verify）') ],
					[ '1', _('校验（推荐）') ],
					[ '0', _('不校验（自签证书）') ]
				])
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

	/* 保存 = 若干条 mail-account 子命令（add + 逐个 set），保存后刷新列表 */
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
				return pfRun([ 'mail-account' ].concat(cmd));
			});
		}, Promise.resolve());

		return withBusy(_('正在保存邮箱账号…'), run).then(function() {
			ui.addNotification(null, E('p', {}, _('已保存邮箱账号「%s」。').format(name)), 'success');
			if (!andTest)
				return null;
			return withBusy(_('正在发送测试邮件…'), pfRun([ 'mail-account', 'test', name ])).then(function(res) {
				var ok = !(res && res.ok === false);
				var msg = (res && (res.message || res.error)) || _('测试邮件已发送。');
				ui.addNotification(null, E('p', {}, (ok ? _('成功：') : _('失败：')) + msg), ok ? 'success' : 'danger');
			});
		}).then(function() {
			return self.refreshAccounts();
		}).catch(function(err) {
			notifyError(err);
			return self.refreshAccounts();
		});
	}
});
