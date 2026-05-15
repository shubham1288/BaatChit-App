'use strict';

const form     = document.getElementById('loginForm');
const alertBox = document.getElementById('alertBox');
const submitBtn = document.getElementById('submitBtn');

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  clearErrors();

  const username = document.getElementById('username').value.trim();
  const password = document.getElementById('password').value;

  if (!username) { setError('usernameError', 'Username is required'); return; }
  if (!password) { setError('passwordError', 'Password is required'); return; }

  setLoading(true);

  try {
    const body = new URLSearchParams({ username, password });
    const res = await fetch('/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body,
    });

    const data = await res.json();

    if (!res.ok) {
      showAlert(data.detail || 'Invalid credentials', 'error');
      return;
    }

    localStorage.setItem('access_token',  data.access_token);
    localStorage.setItem('refresh_token', data.refresh_token);
    localStorage.setItem('user_id',       data.user_id);
    localStorage.setItem('current_user',  username.toLowerCase());
    window.location.href = '/';
  } catch {
    showAlert('Network error. Please try again.', 'error');
  } finally {
    setLoading(false);
  }
});

function setLoading(v) {
  submitBtn.classList.toggle('loading', v);
  submitBtn.disabled = v;
}

function showAlert(msg, type) {
  alertBox.textContent = msg;
  alertBox.className = `alert ${type} show`;
}

function clearErrors() {
  alertBox.className = 'alert';
  ['usernameError', 'passwordError'].forEach(id => {
    document.getElementById(id).textContent = '';
  });
  document.querySelectorAll('.form-input').forEach(el => el.classList.remove('error'));
}

function setError(id, msg) {
  document.getElementById(id).textContent = msg;
  document.getElementById(id).previousElementSibling?.classList.add('error');
}