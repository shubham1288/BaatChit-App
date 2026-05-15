'use strict';

const form     = document.getElementById('signupForm');
const alertBox = document.getElementById('alertBox');
const submitBtn = document.getElementById('submitBtn');
const pwBar    = document.getElementById('pwBar');
const pwInput  = document.getElementById('password');

// Password strength indicator
pwInput.addEventListener('input', () => {
  const val = pwInput.value;
  const strength = getStrength(val);
  const colors = ['', '#f85149', '#d29922', '#3fb950', '#58a6ff'];
  const widths  = ['0%', '25%', '50%', '75%', '100%'];
  pwBar.style.width      = widths[strength];
  pwBar.style.background = colors[strength];
});

function getStrength(pw) {
  let s = 0;
  if (pw.length >= 6)  s++;
  if (pw.length >= 10) s++;
  if (/[A-Z]/.test(pw) && /[a-z]/.test(pw)) s++;
  if (/[^a-zA-Z0-9]/.test(pw)) s++;
  return s;
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  clearErrors();

  const username = document.getElementById('username').value.trim();
  const password = document.getElementById('password').value;

  if (!username) { setError('usernameError', 'Username is required'); return; }
  if (!/^[a-zA-Z0-9_]{3,50}$/.test(username)) {
    setError('usernameError', 'Only letters, numbers, underscores (3–50 chars)');
    return;
  }
  if (password.length < 6) { setError('passwordError', 'Minimum 6 characters'); return; }

  setLoading(true);

  try {
    const res = await fetch('/auth/signup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ 
        username, 
        password
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      showAlert(data.detail || 'Registration failed', 'error');
      return;
    }
    showAlert('Account created! Redirecting…', 'success');
    setTimeout(() => { window.location.href = '/login-page'; }, 1200);
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
}