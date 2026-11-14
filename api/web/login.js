const baseUrl = location.origin;
const errBox = document.getElementById('err');
const $ = (id)=>document.getElementById(id);

function showErr(msg) {
  errBox.textContent = msg || '登录失败';
  errBox.style.display = 'block';
}

document.getElementById('f').addEventListener('submit', async (e) => {
  e.preventDefault();
  errBox.style.display = 'none';

  const username = $('username').value.trim();
  const password = $('password').value;

  if (!username || !password) {
    showErr('请填写用户名和密码');
    return;
  }

  try {
    const res = await fetch(`${baseUrl}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password })
    });

    const text = await res.text();
    let j;
    try { j = JSON.parse(text); } catch { j = { detail: text }; }

    if (!res.ok) {
      console.error('Login failed:', res.status, j);
      showErr(j?.detail || j?.msg || `登录失败（HTTP ${res.status}）`);
      return;
    }

    if (!j?.data?.token) {
      showErr('登录成功但返回格式异常：缺少 token');
      return;
    }

    localStorage.setItem('token', j.data.token);
    localStorage.setItem('loginUser', username);

    // 跳转到控制台
    window.location.assign(`${baseUrl}/web/index.html`);
  } catch (err) {
    console.error(err);
    showErr(err.message || '网络错误');
  }
});
