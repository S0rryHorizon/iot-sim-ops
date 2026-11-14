window.addEventListener('DOMContentLoaded', () => {
    const baseUrl = location.origin;
    const q = (id) => document.getElementById(id);
    const token = localStorage.getItem('token');
    const loginUser = localStorage.getItem('loginUser');
    if (!token || !loginUser) {
        location.href = `${baseUrl}/web/login.html`;
        return;
    }

    // sessionStorage is scoped to this tab; the exact loginUser is part of the key.
    // A pending snapshot contains only the request identity, never credentials.
    const pendingKey = `iot-sim-ops:pending:${encodeURIComponent(loginUser)}`;
    const sameSession = () => localStorage.getItem('token') === token &&
        localStorage.getItem('loginUser') === loginUser;
    const auth = () => ({'Authorization': `Bearer ${token}`});
    const parseResponse = async(res) => {
        const raw = await res.text();
        try { return JSON.parse(raw); }
        catch { throw new Error(raw || `HTTP ${res.status}`); }
    };
    const errorText = (j, fallback) => j?.detail || j?.msg || fallback;
    const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[c]);
    // getRandomValues also works on plain HTTP demo hosts; randomUUID is
    // restricted to secure contexts. Keep a single cryptographic key path.
    const newTransid = () => {
        const bytes = new Uint8Array(16);
        crypto.getRandomValues(bytes);
        bytes[6] = (bytes[6] & 0x0f) | 0x40;
        bytes[8] = (bytes[8] & 0x3f) | 0x80;
        const hex = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
        return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    };

    q('tokenPreview').textContent = `用户：${loginUser} · ${token}`;
    q('copyBtn').onclick = async() => {
        try {
            if (navigator.clipboard && window.isSecureContext) {
                await navigator.clipboard.writeText(token);
            } else {
                const ta = document.createElement('textarea');
                ta.value = token;
                document.body.appendChild(ta);
                ta.select();
                document.execCommand('copy');
                ta.remove();
            }
            alert('已复制');
        } catch (e) { alert('复制失败'); console.error(e); }
    };
    q('logoutBtn').onclick = () => {
        localStorage.removeItem('token');
        localStorage.removeItem('loginUser');
        location.href = `${baseUrl}/web/login.html`;
    };
    // A login in another tab can replace both localStorage values while a
    // request is in flight. Its result must not be shown in this account.
    window.addEventListener('storage', (event) => {
        if ((event.key === 'token' || event.key === 'loginUser' || event.key === null) && !sameSession()) {
            location.href = `${baseUrl}/web/login.html`;
        }
    });

    let currentIccid = '';
    let contextVersion = 0;
    let searchSeq = 0;
    let ordersSeq = 0;
    let usageSeq = 0;
    let statusSeq = 0;
    let purchaseInFlight = false;
    const monthNow = new Date();
    q('month').value = `${monthNow.getFullYear()}-${String(monthNow.getMonth() + 1).padStart(2, '0')}`;
    const selectedMonth = () => q('month').value.trim();
    const view = () => ({iccid: currentIccid, month: selectedMonth(), version: contextVersion});
    const sameView = (v) => sameSession() && contextVersion === v.version &&
        currentIccid === v.iccid && selectedMonth() === v.month;
    const showPurchase = (message) => {
        q('purchaseMsg').textContent = message;
        q('purchaseMsg').style.display = 'block';
    };
    const clearViews = () => {
        contextVersion++;
        ordersSeq++;
        usageSeq++;
        statusSeq++;
        q('orders').innerHTML = '';
        q('ordersMsg').textContent = '';
        q('usageBox').textContent = '';
        q('opMsg').style.display = 'none';
    };

    let pendingReadError = '';
    function readPending() {
        try {
            const raw = sessionStorage.getItem(pendingKey);
            if (!raw) return null;
            const p = JSON.parse(raw);
            if (p?.version === 1 && p.user === loginUser &&
                typeof p.iccid === 'string' && p.iccid &&
                /^[0-9]{4}-(0[1-9]|1[0-2])$/.test(p.month) && p.month.slice(0, 4) !== '0000' &&
                Number.isInteger(p.package_mb) && p.package_mb > 0 && p.package_mb <= 2147483647 &&
                p.product_id === `pkg_${p.package_mb}m` && p.pay_amount_cent === p.package_mb &&
                typeof p.transid === 'string' &&
                /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(p.transid)) return p;
            pendingReadError = '待确认记录格式损坏；请先核查已有订单，暂不允许新购买。';
        } catch (e) {
            pendingReadError = '无法读取待确认记录；请先核查已有订单，暂不允许新购买。';
            console.error('Cannot restore pending purchase', e);
        }
        return null;
    }
    let pending = readPending();
    function renderPending(note = '') {
        q('btnBuy').disabled = purchaseInFlight || Boolean(pendingReadError);
        q('btnBuy').textContent = pending ? '重试待确认购买' : '购买';
        if (pendingReadError) { showPurchase(pendingReadError); return; }
        if (pending) {
            showPurchase(`${note || '待确认购买；刷新页面不会自动重试。'} 卡 ${pending.iccid}，${pending.month}，${pending.package_mb} MB；重试固定使用原参数和 X-TransId。`);
        }
    }
    renderPending();

    q('month').addEventListener('input', () => clearViews());
    q('month').addEventListener('change', () => {
        if (currentIccid && /^[0-9]{4}-(0[1-9]|1[0-2])$/.test(selectedMonth())) {
            loadOrders();
            loadUsage();
        }
    });

    q('searchBtn').onclick = async() => {
        const seq = ++searchSeq;
        currentIccid = '';
        q('simInfo').textContent = '尚未选择卡';
        clearViews();
        const qs = new URLSearchParams();
        for (const field of ['iccid', 'imsi', 'msisdn']) {
            const value = q(field).value.trim();
            if (value) qs.set(field, value);
        }
        if (!qs.toString()) {
            q('searchMsg').textContent = '请至少输入一个字段';
            return;
        }
        q('searchMsg').textContent = '查询中...';
        try {
            const res = await fetch(`${baseUrl}/sims/search?${qs}`, {headers: auth()});
            const j = await parseResponse(res);
            if (seq !== searchSeq || !sameSession()) return;
            if (!res.ok) throw new Error(errorText(j, '查询失败'));
            const d = j.data;
            currentIccid = d.iccid;
            clearViews();
            q('searchMsg').textContent = '';
            q('simInfo').innerHTML = `
                <div class="grid">
                  <div><div class="muted">ICCID</div><div>${escapeHtml(d.iccid || '-')}</div></div>
                  <div><div class="muted">IMSI</div><div>${escapeHtml(d.imsi || '-')}</div></div>
                  <div><div class="muted">MSISDN</div><div>${escapeHtml(d.msisdn || '-')}</div></div>
                  <div><div class="muted">状态</div><div class="status ${d.status === 'ACTIVE' ? 'ok' : 'bad'}">${escapeHtml(d.status)}</div></div>
                </div>`;
            loadOrders();
            loadUsage();
        } catch (e) {
            if (seq === searchSeq && sameSession()) q('searchMsg').textContent = e.message || '查询失败';
        }
    };

    async function fetchStatus() {
        if (!currentIccid) return;
        const v = view();
        const seq = ++statusSeq;
        const res = await fetch(`${baseUrl}/sims/${encodeURIComponent(v.iccid)}/status`, {headers: auth()});
        const j = await parseResponse(res);
        if (!sameView(v) || seq !== statusSeq) return;
        if (!res.ok) throw new Error(errorText(j, '刷新失败'));
        const el = q('simInfo').querySelector('.status');
        if (el) {
            el.textContent = j.data.status;
            el.className = `status ${j.data.status === 'ACTIVE' ? 'ok' : 'bad'}`;
        }
        return true;
    }
    q('btnRefresh').onclick = async() => {
        const btn = q('btnRefresh');
        const v = view();
        q('opMsg').style.display = 'none';
        btn.disabled = true;
        try {
            if (await fetchStatus() && sameView(v)) {
                q('opMsg').textContent = '已刷新';
                q('opMsg').style.display = 'block';
            }
        } catch (e) {
            if (sameView(v)) {
                q('opMsg').textContent = e.message || '刷新失败';
                q('opMsg').style.display = 'block';
            }
        } finally { btn.disabled = false; }
    };
    async function changeStatus(action) {
        if (!currentIccid) return;
        const v = view();
        const seq = ++statusSeq;
        q('opMsg').style.display = 'none';
        try {
            const res = await fetch(`${baseUrl}/sims/${encodeURIComponent(v.iccid)}/status`, {
                method: 'PATCH', headers: {...auth(), 'Content-Type': 'application/json'},
                body: JSON.stringify({action})
            });
            const j = await parseResponse(res);
            if (!sameView(v) || seq !== statusSeq) return;
            if (!res.ok) throw new Error(errorText(j, '操作失败'));
            q('opMsg').textContent = `已执行 ${action}`;
            q('opMsg').style.display = 'block';
            fetchStatus().catch((e) => {
                if (sameView(v)) q('opMsg').textContent = `状态刷新失败：${e.message}`;
            });
        } catch (e) {
            if (sameView(v)) {
                q('opMsg').textContent = e.message || '操作失败';
                q('opMsg').style.display = 'block';
            }
        }
    }
    q('btnSuspend').onclick = () => changeStatus('SUSPEND');
    q('btnResume').onclick = () => changeStatus('RESUME');

    async function loadUsage() {
        if (!currentIccid) return;
        const v = view();
        const seq = ++usageSeq;
        try {
            const res = await fetch(`${baseUrl}/sims/${encodeURIComponent(v.iccid)}/usage?month=${encodeURIComponent(v.month)}`, {headers: auth()});
            const j = await parseResponse(res);
            if (!sameView(v) || seq !== usageSeq) return;
            if (!res.ok) throw new Error(errorText(j, '无数据'));
            const d = j.data;
            q('usageBox').textContent = `已用 ${d.used_mb} MB / 套餐 ${d.package_mb} MB，剩余 ${Math.max(0, d.package_mb - d.used_mb)} MB`;
        } catch (e) {
            if (sameView(v) && seq === usageSeq) q('usageBox').textContent = e.message || '查询失败';
        }
    }
    q('btnUsage').onclick = loadUsage;

    async function loadOrders() {
        if (!currentIccid) return;
        const v = view();
        const seq = ++ordersSeq;
        try {
            const url = `${baseUrl}/sims/${encodeURIComponent(v.iccid)}/purchases?month=${encodeURIComponent(v.month)}&limit=20&offset=0`;
            const res = await fetch(url, {headers: auth()});
            const j = await parseResponse(res);
            if (!sameView(v) || seq !== ordersSeq) return;
            if (!res.ok) throw new Error(errorText(j, '订单查询失败'));
            const rows = (j.data.items || []).map(o => `<tr><td>${escapeHtml(o.order_id)}</td><td>${escapeHtml(o.month)}</td><td>${escapeHtml(o.package_mb ?? '-')}</td><td>${escapeHtml(o.status)}</td><td>${escapeHtml(o.created_at || '')}</td></tr>`).join('');
            q('orders').innerHTML = rows || '<tr><td colspan="5" class="muted">暂无订单</td></tr>';
            q('ordersMsg').textContent = '';
        } catch (e) {
            if (sameView(v) && seq === ordersSeq) q('ordersMsg').textContent = `订单刷新失败：${e.message || '网络错误'}`;
        }
    }

    q('btnBuy').onclick = async() => {
        if (purchaseInFlight || pendingReadError || !sameSession()) return;
        if (!pending) {
            if (!currentIccid) { showPurchase('请先选择卡'); return; }
            const month = selectedMonth();
            const rawMb = q('pkgMb').value.trim();
            const mb = Number(rawMb);
            if (!/^[1-9][0-9]*$/.test(rawMb) || !Number.isSafeInteger(mb) || mb > 2147483647 ||
                !/^[0-9]{4}-(0[1-9]|1[0-2])$/.test(month) || month.slice(0, 4) === '0000') {
                showPurchase('请输入有效月份和 1–2147483647 MB 的加包流量');
                return;
            }
            try {
                const snapshot = {version: 1, user: loginUser, iccid: currentIccid,
                    month, package_mb: mb, product_id: `pkg_${mb}m`,
                    pay_amount_cent: mb, transid: newTransid()};
                // A failed storage write must stop the POST; a lost response needs this exact key.
                sessionStorage.setItem(pendingKey, JSON.stringify(snapshot));
                pending = snapshot;
            } catch (e) {
                showPurchase('无法生成或保存待确认购买，未提交；请检查浏览器安全随机数与会话存储。');
                return;
            }
        }
        const snapshot = pending;
        purchaseInFlight = true;
        renderPending('购买提交中，请勿重复点击。');
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 15000);
        try {
            const res = await fetch(`${baseUrl}/sims/${encodeURIComponent(snapshot.iccid)}/purchase`, {
                method: 'POST',
                headers: {...auth(), 'Content-Type': 'application/json', 'X-TransId': snapshot.transid},
                body: JSON.stringify({month: snapshot.month, package_mb: snapshot.package_mb,
                    product_id: snapshot.product_id, pay_amount_cent: snapshot.pay_amount_cent}),
                signal: controller.signal
            });
            const j = await parseResponse(res);
            if (!sameSession()) return;
            if (!res.ok) {
                const detail = errorText(j, `HTTP ${res.status}`);
                // The backend rolls back capacity overflow; a transid conflict is
                // different and stays pending for manual investigation.
                if (res.status === 400 || res.status === 422 ||
                    (res.status === 409 && detail === 'package_mb capacity exceeded')) {
                    showPurchase(`购买被明确拒绝：${detail}。可修改后重新提交。`);
                    try {
                        sessionStorage.removeItem(pendingKey);
                        pending = null;
                    } catch (e) {
                        pendingReadError = '待确认记录清理失败；请先核查会话存储，暂不允许新购买。';
                        showPurchase(`购买被明确拒绝：${detail}。${pendingReadError}`);
                    }
                } else {
                    renderPending(res.status === 401
                        ? '登录已失效；请重新登录同一账号，再重试。'
                        : `结果未确认（${detail}）；请核查后使用原快照重试。`);
                }
                return;
            }
            if (!j?.data?.order_id) throw new Error('成功响应缺少订单号');
            // A successful receipt is independent of the following GET requests.
            const receipt = `下单成功：${j.data.order_id}（卡 ${snapshot.iccid}，${snapshot.month}，${snapshot.package_mb} MB）`;
            showPurchase(receipt);
            try {
                sessionStorage.removeItem(pendingKey);
                pending = null;
            } catch (e) {
                pendingReadError = '待确认记录清理失败；请先核查会话存储，暂不允许新购买。';
                showPurchase(`${receipt}。${pendingReadError}`);
            }
            if (currentIccid === snapshot.iccid && selectedMonth() === snapshot.month) {
                loadOrders();
                loadUsage();
            }
        } catch (e) {
            if (sameSession()) renderPending(`结果未确认（${e.name === 'AbortError' ? '请求超时' : e.message || '网络错误'}）；请使用原快照重试。`);
        } finally {
            clearTimeout(timeout);
            purchaseInFlight = false;
            q('btnBuy').disabled = Boolean(pendingReadError);
            q('btnBuy').textContent = pending ? '重试待确认购买' : '购买';
        }
    };
});
