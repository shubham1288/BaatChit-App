'use strict';

// ─── Token helpers ───────────────────────────────────────────────────────────
function getToken()   { return localStorage.getItem('access_token'); }
function getRefresh() { return localStorage.getItem('refresh_token'); }
function getUser()    { return localStorage.getItem('current_user'); }

function requireAuth() {
  if (!getToken()) window.location.href = '/login-page';
}

// ─── Refresh access token ────────────────────────────────────────────────────
async function refreshAccessToken() {
  const refresh = getRefresh();
  if (!refresh) { logout(); return null; }
  try {
    const res = await fetch('/auth/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refresh }),
    });
    if (!res.ok) { logout(); return null; }
    const data = await res.json();
    localStorage.setItem('access_token', data.access_token);
    return data.access_token;
  } catch { logout(); return null; }
}

// ─── Authenticated fetch ─────────────────────────────────────────────────────
async function authFetch(url, opts = {}) {
  let token = getToken();
  opts.headers = { ...opts.headers, Authorization: `Bearer ${token}` };
  let res = await fetch(url, opts);
  if (res.status === 401) {
    console.warn("Unauthorized! Attempting token refresh...");
    token = await refreshAccessToken();
    if (!token) return res;
    opts.headers.Authorization = `Bearer ${token}`;
    res = await fetch(url, opts);
  }
  if (!res.ok) {
    console.error(`authFetch failed for ${url}:`, res.status, await res.clone().text());
  }
  return res;
}

// ─── State ───────────────────────────────────────────────────────────────────
let ws = null;
let wsReconnectTimer = null;
let activeTab = 'direct';        // 'direct' | 'groups'
let activeTarget = null;          // { type, id/username, name }
let allContacts = [];             // search results
let activeConversations = [];     // users with chat history
let allGroups = [];               // all groups from /groups
let searchTimeout = null;
let messageSkip = 0;
const MESSAGE_LIMIT = 50;

// Reply State
let currentReplyTo = null; // { id, content, sender }
let editingMessageId = null; // Message ID being edited

// Intersection Observer for Read Receipts
const readObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
        if (entry.isIntersecting) {
            const msgEl = entry.target;
            const msgId = parseInt(msgEl.id.replace('msg-', ''));
            const isIncoming = msgEl.classList.contains('incoming');
            
            if (isIncoming && ws && ws.readyState === WebSocket.OPEN) {
                // Check if already read (optional optimization)
                ws.send(JSON.stringify({
                    type: 'mark_read',
                    id: msgId,
                    chat_type: activeTarget.type
                }));
            }
            readObserver.unobserve(msgEl);
        }
    });
}, { threshold: 0.5 });


requireAuth();
document.getElementById('currentUsername').textContent = getUser() || '…';
const me = getUser() || '';

// Initially load groups and active conversations
loadGroups();
loadActiveConversations();
connectWebSocket();

// ─── WebSocket ───────────────────────────────────────────────────────────────
function connectWebSocket() {
  const token = getToken();
  if (!token) return;

  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${protocol}//${location.host}/ws?token=${encodeURIComponent(token)}`);

  ws.onopen = () => {
    document.getElementById('connBanner').classList.remove('show');
    if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null; }
  };

  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.type === 'private_message')  handleIncomingPrivate(data);
    else if (data.type === 'group_message') handleIncomingGroup(data);
    else if (data.type === 'message_edited') handleMessageEdited(data);
    else if (data.type === 'message_deleted') handleMessageDeleted(data);
    else if (data.type === 'message_reacted') handleMessageReacted(data);
    else if (data.type === 'message_status_update') handleMessageStatusUpdate(data);
    else if (data.type === 'error')       showToast(data.detail, 'error');

  };

  ws.onclose = () => scheduleReconnect();
  ws.onerror = () => ws.close();
}

function scheduleReconnect() {
  if (wsReconnectTimer) return;
  console.log("Scheduling WS reconnect...");
  document.getElementById('connBanner').classList.add('show');
  wsReconnectTimer = setTimeout(() => {
    wsReconnectTimer = null;
    connectWebSocket();
  }, 3000);
}

// ─── Message Context Menu ────────────────────────────────────────────────────
let _ctxMsgData = null; // current message data for context menu

function openMsgCtxMenu(event, msgData) {
  event.preventDefault();
  event.stopPropagation();
  console.log("openMsgCtxMenu called for:", msgData.id, msgData);

  const menu = document.getElementById('msgCtxMenu');
  if (!menu) {
    console.error("Context menu element #msgCtxMenu not found!");
    return;
  }

  _ctxMsgData = msgData;
  const { id, content, sender, isMe, isGroup, isDeleted } = msgData;

  const emojis = ['👍', '❤️', '😂', '😮', '😢', '🙏'];
  const reactionsHtml = `
    <div class="ctx-reactions">
      ${emojis.map(e => `<button class="ctx-reaction-btn" onclick="reactToMessage(${id},'${e}')" title="React ${e}">${e}</button>`).join('')}
      <button class="ctx-reaction-more" onclick="showMoreReactions(${id})" title="More reactions">+</button>
    </div>`;

  let itemsHtml = `<div class="ctx-menu-list">`;
  if (!isDeleted) {
    itemsHtml += ctxItem(`setReply(${id}, '${escJs(content)}', '${escJs(sender)}')`, iconSvg('reply'), 'Reply');
    itemsHtml += ctxItem(`copyMessage('${escJs(content)}')`, iconSvg('copy'), 'Copy');
    itemsHtml += ctxItem(`forwardMessage('${escJs(content)}')`, iconSvg('forward'), 'Forward');

    if (isGroup && !isMe) {
      itemsHtml += ctxItem(`replyPrivately('${escJs(sender)}')`, iconSvg('private'), 'Reply privately', 'ctx-private-reply');
    }

    if (isMe) {
      itemsHtml += `<div class="ctx-separator"></div>`;
      itemsHtml += ctxItem(`initiateEdit(${id}, '${escJs(content)}')`, iconSvg('edit'), 'Edit');
    }

    itemsHtml += `<div class="ctx-separator"></div>`;
    itemsHtml += ctxItem(`deleteMessage(${id})`, iconSvg('delete'), 'Delete', 'ctx-danger');
  } else {
    itemsHtml += `<div class="ctx-menu-item" style="color:var(--text-muted);cursor:default">Deleted message</div>`;
  }
  itemsHtml += `</div>`;

  menu.innerHTML = reactionsHtml + itemsHtml;

  // Position
  menu.style.display = 'block';
  menu.style.visibility = 'hidden';

  requestAnimationFrame(() => {
    const menuW = menu.offsetWidth || 240;
    const menuH = menu.offsetHeight || 360;
    
    let x = event.clientX, y = event.clientY;
    
    if (x + menuW > window.innerWidth) {
      x = window.innerWidth - menuW - 10;
      menu.classList.add('from-left');
    } else {
      menu.classList.remove('from-left');
    }
    
    if (y + menuH > window.innerHeight) {
      y = window.innerHeight - menuH - 10;
    }

    menu.style.left = Math.max(10, x) + 'px';
    menu.style.top  = Math.max(10, y) + 'px';
    menu.style.visibility = 'visible';
    console.log("Menu positioned at:", x, y);
  });
}

function ctxItem(onclick, iconHtml, label, extraClass = '') {
  return `<button class="ctx-menu-item ${extraClass}" onclick="closeCtxMenu();${onclick}">
    <span class="ctx-icon">${iconHtml}</span>
    <span>${label}</span>
  </button>`;
}

function closeCtxMenu() {
  const menu = document.getElementById('msgCtxMenu');
  if (menu) menu.style.display = 'none';
}

// Close on outside click
window.addEventListener('click', closeCtxMenu);
window.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeCtxMenu(); });

// ─── Context Menu Actions (stubs + real) ─────────────────────────────────────
function copyMessage(content) {
  navigator.clipboard.writeText(content).then(
    () => showToast('Copied to clipboard', 'success'),
    () => showToast('Copy failed', 'error')
  );
}


let forwardContent = null;

function forwardMessage(content) {
  forwardContent = content;
  document.getElementById('forwardSearchInput').value = '';
  document.getElementById('forwardModal').classList.add('open');
  filterForwardList('');
  closeCtxMenu();
}

function filterForwardList(query) {
  const list = document.getElementById('forwardList');
  const q = query.trim().toLowerCase();
  
  if (!q) {
    list.innerHTML = '<div class="empty-list">Type a name to search...</div>';
    return;
  }

  const targetsMap = new Map();
  
  // Add active conversations that match
  activeConversations.forEach(c => {
    targetsMap.set(`direct-${c.partner_username}`, { 
      id: c.partner_username, 
      name: c.partner_username, 
      type: 'direct' 
    });
  });

  // Add search results that match
  allContacts.forEach(c => {
    targetsMap.set(`direct-${c.username}`, { 
      id: c.username, 
      name: c.username, 
      type: 'direct' 
    });
  });

  // Add groups that match
  allGroups.forEach(g => {
    targetsMap.set(`group-${g.id}`, { 
      id: g.id, 
      name: g.name, 
      type: 'group' 
    });
  });
  
  const filtered = Array.from(targetsMap.values()).filter(t => 
    t.name.toLowerCase().includes(q)
  );
  
  if (!filtered.length) {
    list.innerHTML = '<div class="empty-list">No matches found</div>';
    return;
  }
  
  list.innerHTML = filtered.map(t => `
    <div class="member-item" style="cursor:pointer" onclick="executeForward('${t.type}', '${t.id}', '${escJs(t.name)}')">
      <div class="member-info">
        <span class="member-name">${escHtml(t.name)}</span>
        <span class="member-role">${t.type === 'direct' ? 'User' : 'Group'}</span>
      </div>
    </div>
  `).join('');
}

async function executeForward(type, targetId, targetName) {
  const content = forwardContent;
  closeModal('forwardModal');
  if (!content) return;
  
  try {
    if (type === 'direct') {
      switchTab('direct');
      await openDirectChat(targetId);
    } else {
      switchTab('groups');
      await openGroupChat(parseInt(targetId), targetName);
    }
    
    // Small delay to ensure UI is ready
    setTimeout(() => {
      const input = document.getElementById('msgInput');
      input.value = content;
      sendMessage();
      forwardContent = null;
    }, 100);
  } catch (e) {
    console.error("Forward failed:", e);
    showToast("Failed to forward message", "error");
  }
}


function pinMessage(id) {
  showToast('Pin: coming soon!', 'info');
}

function starMessage(id) {
  showToast('Star: coming soon!', 'info');
}


function reactToMessage(id, emoji) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({
      type: _ctxMsgData.isGroup ? 'react_group' : 'react_private',
      id: id,
      emoji: emoji
    }));
  }
  closeCtxMenu();
}


function showMoreReactions(id) {
  showToast('More reactions: coming soon!', 'info');
}


function replyPrivately(username) {
  closeCtxMenu();
  const content = _ctxMsgData.content;
  const sender = _ctxMsgData.sender;
  const msgId = _ctxMsgData.id;
  
  switchTab('direct');
  openDirectChat(username).then(() => {
      setReply(msgId, content, sender);
  });
  showToast(`Replying privately to ${username}`, 'info');
}


// ─── SVG icon helper ─────────────────────────────────────────────────────────
function iconSvg(name) {
  const icons = {
    reply:   `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M10 9V5l-7 7 7 7v-4.1c5 0 8.5 1.6 11 5.1-1-5-4-10-11-11z"/></svg>`,
    copy:    `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/></svg>`,
    react:   `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm3.5-9c.83 0 1.5-.67 1.5-1.5S16.33 8 15.5 8 14 8.67 14 9.5s.67 1.5 1.5 1.5zm-7 0c.83 0 1.5-.67 1.5-1.5S9.33 8 8.5 8 7 8.67 7 9.5 7.67 11 8.5 11zm3.5 6.5c2.33 0 4.31-1.46 5.11-3.5H6.89c.8 2.04 2.78 3.5 5.11 3.5z"/></svg>`,
    forward: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 8V4l8 8-8 8v-4H4V8z"/></svg>`,
    pin:     `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M16 12V4h1V2H7v2h1v8l-2 2v2h5.2v6h1.6v-6H18v-2l-2-2z"/></svg>`,
    star:    `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z"/></svg>`,
    edit:    `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34c-.39-.39-1.02-.39-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/></svg>`,
    delete:  `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>`,
    private: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z"/></svg>`,
    info:    `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z"/></svg>`
  };
  return icons[name] || '';
}

// Close menu on click outside
// Global listener to close context menu
window.addEventListener('click', () => {
    closeCtxMenu();
});

// ─── Tab switching ───────────────────────────────────────────────────────────
function switchTab(tab) {
  activeTab = tab;
  document.getElementById('tabDirect').classList.toggle('active', tab === 'direct');
  document.getElementById('tabGroups').classList.toggle('active', tab === 'groups');
  document.getElementById('newGroupBtn').style.display = tab === 'groups' ? '' : 'none';
  document.getElementById('searchInput').value = '';
  renderList();
}

// ─── Contacts ────────────────────────────────────────────────────────────────
// ─── Contacts ────────────────────────────────────────────────────────────────
async function loadContacts(query = '') {
  try {
    const res = await authFetch(`/users?search=${encodeURIComponent(query)}`);
    if (!res.ok) return;
    allContacts = await res.json();
    if (activeTab === 'direct') renderList(query);
  } catch {}
}

async function loadGroups() {
  try {
    const res = await authFetch('/groups');
    if (!res.ok) return;
    allGroups = await res.json();
    if (activeTab === 'groups') renderList();
  } catch {}
}

async function loadActiveConversations() {
  try {
    const res = await authFetch('/conversations');
    if (!res.ok) return;
    activeConversations = await res.json();
    if (activeTab === 'direct' && !document.getElementById('searchInput').value.trim()) {
      renderList();
    }
  } catch {}
}

function filterList(query) {
  if (activeTab === 'groups') {
    renderList(query);
  } else {
    // Debounce server search for direct
    if (searchTimeout) clearTimeout(searchTimeout);
    if (!query.trim()) {
      allContacts = [];
      renderList();
      return;
    }
    searchTimeout = setTimeout(() => {
      loadContacts(query);
    }, 300);
  }
}

function renderList(query = '') {
  const list = document.getElementById('contactList');
  const q = query.toLowerCase();

  if (activeTab === 'direct') {
    if (!query.trim()) {
      if (!activeConversations.length) {
        list.innerHTML = '<div class="empty-list">Search for a username to start chatting</div>';
        return;
      }
      list.innerHTML = activeConversations.map(c => `
        <div class="contact-item ${activeTarget?.username === c.partner_username ? 'active' : ''}"
             id="contact-${c.partner_username}"
             onclick="openDirectChat('${escJs(c.partner_username)}')">

          <div class="contact-info">
            <div class="contact-name">${escHtml(c.partner_username)}</div>
            <div class="contact-preview">${escHtml(c.last_message || '')}</div>
          </div>
        </div>
      `).join('');
      return;
    }
    if (!allContacts.length) {
      list.innerHTML = '<div class="empty-list">No user found</div>';
      return;
    }
    list.innerHTML = allContacts.map(u => `
      <div class="contact-item ${activeTarget?.username === u.username ? 'active' : ''}"
           id="contact-${u.username}"
           onclick="openDirectChat('${u.username}')">

        <div class="contact-info">
          <div class="contact-name">${u.username}</div>
          <div class="contact-preview">Click to chat</div>
        </div>
      </div>
    `).join('');
  } else {
    const filtered = allGroups.filter(g =>
      g.name.toLowerCase().includes(q)
    );
    if (!filtered.length) {
      list.innerHTML = '<div class="empty-list">No groups yet. Create one!</div>';
      return;
    }
    list.innerHTML = filtered.map(g => `
      <div class="contact-item ${activeTarget?.id === g.id ? 'active' : ''}"
           id="group-${g.id}"
           onclick="openGroupChat(${g.id}, '${escJs(g.name)}')">

        <div class="contact-info">
          <div class="contact-name">${escHtml(g.name)}</div>
          <div class="contact-preview">Group</div>
        </div>
      </div>
    `).join('');
  }
}

// ─── Open Chats ─────────────────────────────────────────────────────────────
async function openDirectChat(username) {
  activeTarget = { type: 'direct', username };
  messageSkip = 0;

  document.getElementById('placeholder').style.display  = 'none';
  const ac = document.getElementById('activeChat');
  ac.style.display = 'flex';

// document.getElementById('chatAvatar').textContent = username[0].toUpperCase();
  document.getElementById('chatName').textContent = username;
  document.getElementById('chatSub').textContent = 'Private chat';
  document.getElementById('chatBadge').style.display = 'none';
  document.getElementById('viewMembersBtn').style.display = 'none';

  // Mark active in sidebar
  document.querySelectorAll('.contact-item').forEach(el => el.classList.remove('active'));
  document.getElementById(`contact-${username}`)?.classList.add('active');

  clearMessages();
  await fetchPrivateHistory(username);
  scrollToBottom();

  // Options menu setup
  document.getElementById('exitGroupBtn').style.display = 'none';
  document.getElementById('resolveGroupBtn').style.display = 'none';
  document.getElementById('deleteChatBtn').style.display = '';
}

async function openGroupChat(groupId, groupName) {
  activeTarget = { type: 'group', id: groupId, name: groupName };
  messageSkip = 0;

  document.getElementById('placeholder').style.display  = 'none';
  const ac = document.getElementById('activeChat');
  ac.style.display = 'flex';

// document.getElementById('chatAvatar').textContent = '#';
// document.getElementById('chatAvatar').style.background = 'linear-gradient(135deg,#1f6feb,#58a6ff)';
  document.getElementById('chatName').textContent = groupName;
  document.getElementById('chatSub').textContent = 'Group chat';
  const badge = document.getElementById('chatBadge');
  badge.style.display = '';
  badge.textContent = 'Add Member';
  badge.style.cursor = 'pointer';
  badge.onclick = () => openAddMemberModal(groupId);

  document.getElementById('viewMembersBtn').style.display = 'flex';

  document.querySelectorAll('.contact-item').forEach(el => el.classList.remove('active'));
  document.getElementById(`group-${groupId}`)?.classList.add('active');

  clearMessages();
  await fetchGroupHistory(groupId);
  scrollToBottom();

  // Options menu setup
  document.getElementById('exitGroupBtn').style.display = '';
  document.getElementById('deleteChatBtn').style.display = '';
  
  // Check if admin to show resolve button
  const group = allGroups.find(g => g.id === groupId);
  const me = getUser();
  // We need to know who the admin is. Let's fetch members to find out or use group object if it has admin_id
  // GroupOut has admin_id
  if (group && group.admin_id && parseInt(localStorage.getItem('user_id')) === group.admin_id) {
    document.getElementById('resolveGroupBtn').style.display = '';
  } else {
    document.getElementById('resolveGroupBtn').style.display = 'none';
  }
}

// ─── Message History ─────────────────────────────────────────────────────────
async function fetchPrivateHistory(username) {
  try {
    const res = await authFetch(
      `/conversations/${username}?skip=${messageSkip}&limit=${MESSAGE_LIMIT}`
    );
    if (!res.ok) return;
    const data = await res.json();
    const container = document.getElementById('messagesContainer');

    if (data.total > messageSkip + MESSAGE_LIMIT) {
      document.getElementById('loadMoreBtn').style.display = '';
    }

    const existing = container.querySelectorAll('.msg-row');
    for (const m of data.messages) {
      await appendPrivateMessage(m, true);
    }
    messageSkip += data.messages.length;
  } catch {}
}

async function fetchGroupHistory(groupId) {
  try {
    const res = await authFetch(
      `/groups/${groupId}/messages?skip=${messageSkip}&limit=${MESSAGE_LIMIT}`
    );
    if (!res.ok) {
        console.error("Failed to fetch group history:", await res.text());
        return;
    }
    const data = await res.json();
    const msgs = data.messages || [];

    if (messageSkip === 0) {
        clearMessages();
        msgs.reverse().forEach(m => appendGroupMessage(m));
    } else {
        msgs.forEach(m => appendGroupMessage(m, true));
    }

    const loadMoreBtn = document.getElementById('loadMoreBtn');
    if (msgs.length >= MESSAGE_LIMIT) {
      loadMoreBtn.style.display = 'block';
    } else {
      loadMoreBtn.style.display = 'none';
    }
  } catch (e) {
    console.error("Error fetching group history:", e);
  }
}

async function loadMoreMessages() {
  if (!activeTarget) return;
  if (activeTarget.type === 'direct') await fetchPrivateHistory(activeTarget.username);
  else await fetchGroupHistory(activeTarget.id);
}

// ─── Send Message ─────────────────────────────────────────────────────────────
async function sendMessage() {
  const input = document.getElementById('msgInput');
  const content = input.value.trim();
  if (!content || !activeTarget) return;

  input.value = '';
  input.style.height = 'auto';

  if (!ws || ws.readyState !== WebSocket.OPEN) {
    if (editingMessageId) {
      sendEditFallback(content, null);
    } else {
      sendFallback(content, null);
    }
    return;
  }

  if (editingMessageId) {
    ws.send(JSON.stringify({
      type: activeTarget.type === 'direct' ? 'edit_private' : 'edit_group',
      id: editingMessageId,
      content: content,
      group_id: activeTarget.type === 'group' ? activeTarget.id : undefined
    }));
    cancelEdit();
    return;
  }

  if (activeTarget.type === 'direct') {
    ws.send(JSON.stringify({
      type: 'private',
      receiver: activeTarget.username,
      content: content,
      reply_to_id: currentReplyTo?.id
    }));
    setTimeout(loadActiveConversations, 500);
  } else {
    ws.send(JSON.stringify({
      type: 'group',
      group_id: activeTarget.id,
      content: content,
      reply_to_id: currentReplyTo?.id
    }));
  }
  cancelReply();
}

// ─── Reply Context ────────────────────────────────────────────────────────────
function setReply(id, content, sender) {
  currentReplyTo = { id, content, sender };
  document.getElementById('replyPreviewSender').textContent = sender;
  document.getElementById('replyPreviewText').textContent = content;
  document.getElementById('replyPreview').style.display = 'flex';
  document.getElementById('msgInput').focus();
}

function cancelReply() {
  currentReplyTo = null;
  document.getElementById('replyPreview').style.display = 'none';
}

function scrollToMessage(id) {
  const el = document.getElementById(`msg-${id}`);
  if (el) {
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.classList.add('highlight');
    setTimeout(() => el.classList.remove('highlight'), 2000);
  }
}

async function sendFallback(content, _unused) {
  try {
    if (activeTarget.type === 'direct') {
      const res = await authFetch('/conversations/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ receiver: activeTarget.username, content, reply_to_id: currentReplyTo?.id }),
      });
      if (res.ok) handleIncomingPrivate(await res.json());
    } else {
      const res = await authFetch(`/groups/${activeTarget.id}/messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content, reply_to_id: currentReplyTo?.id }),
      });
      if (res.ok) handleIncomingGroup(await res.json());
    }
    cancelReply();
  } catch (e) {
    showToast('Failed to send message', 'error');
  }
}

// ─── Incoming WS Messages ─────────────────────────────────────────────────────
async function handleIncomingPrivate(data) {
  // Normalize field names (REST uses sender_username, WS uses sender)
  const sender = data.sender || data.sender_username;
  const receiver = data.receiver || data.receiver_username;
  const timestamp = data.timestamp || data.created_at;

  const isRelevant = (
    activeTarget?.type === 'direct' &&
    (sender === activeTarget.username || receiver === activeTarget.username)
  );
  
  if (isRelevant) {
    await appendPrivateMessage({
      id: data.id,
      sender_id: sender === me ? -1 : 0,
      sender_username: sender,
      receiver_username: receiver,
      content: data.content,
      status: data.status,
      created_at: timestamp,
      reply_to_id: data.reply_to_id,
      reply_content: data.reply_content,
      reply_sender: data.reply_sender,
      is_edited: data.is_edited,
      is_deleted: data.is_deleted,
      reactions: data.reactions
    }, false);
    scrollToBottom();
    if (sender !== me) {
      // mark_read is now handled by IntersectionObserver
    }
    loadActiveConversations();

  } else if (sender !== me) {
    showToast(`💬 ${sender}: ${data.content.slice(0, 60)}`, 'info');
    loadActiveConversations();
  }
}

async function handleIncomingGroup(data) {
  const sender = data.sender || data.sender_username;
  const timestamp = data.timestamp || data.created_at;
  const isRelevant = activeTarget?.type === 'group' && activeTarget.id === data.group_id;

  if (isRelevant) {
    await appendGroupMessage({
      id: data.id,
      group_id: data.group_id,
      sender_id: sender === me ? -1 : 0,
      sender_username: sender,
      content: data.content,
      created_at: timestamp,
      reply_to_id: data.reply_to_id,
      reply_content: data.reply_content,
      reply_sender: data.reply_sender,
      is_edited: data.is_edited,
      is_deleted: data.is_deleted,
      reactions: data.reactions,
      status: data.status
    }, false);
    scrollToBottom();
  } else if (sender !== me) {

    const grp = allGroups.find(g => g.id === data.group_id);
    showToast(`👥 ${grp?.name || 'Group'}: ${data.content.slice(0, 60)}`, 'info');
  }
}

async function handleMessageEdited(data) {
  const el = document.getElementById(`msg-${data.id}`);
  if (!el) return;

  let content = data.content;

  const textEl = el.querySelector('.msg-text');
  if (textEl) {
    textEl.textContent = content;
    if (!textEl.querySelector('.edited-tag')) {
      textEl.insertAdjacentHTML('beforeend', '<span class="edited-tag">(edited)</span>');
    }
  }
}

function handleMessageDeleted(data) {
  const el = document.getElementById(`msg-${data.id}`);
  if (!el) return;
  el.remove();
}

function handleMessageStatusUpdate(data) {
    const el = document.getElementById(`msg-${data.id}`);
    if (!el) return;
    const tick = el.querySelector('.status-tick');
    if (tick) {
        tick.className = `status-tick ${data.status}`;
        tick.innerHTML = statusIcon(data.status);
    }
}


function initiateEdit(id, content) {
  cancelReply();
  editingMessageId = id;
  const input = document.getElementById('msgInput');
  input.value = content;
  document.getElementById('editPreviewText').textContent = content;
  document.getElementById('editPreview').style.display = 'flex';
  autoResize(input);
  input.focus();
}

function cancelEdit() {
  editingMessageId = null;
  document.getElementById('editPreview').style.display = 'none';
  document.getElementById('msgInput').value = '';
  document.getElementById('msgInput').style.height = 'auto';
}

async function deleteMessage(id) {
  console.log("deleteMessage called for id:", id);
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({
      type: activeTarget.type === 'direct' ? 'delete_private' : 'delete_group',
      id: id
    }));
  } else {
    try {
        const url = activeTarget.type === 'direct' ? `/conversations/messages/${id}` : `/groups/messages/${id}`;
        const res = await authFetch(url, { method: 'DELETE' });
        if (!res.ok) {
            console.error("Delete REST failed:", await res.text());
        }
    } catch (e) {
        console.error("Delete REST error:", e);
    }
  }
}

async function editMessage(id, newContent) {
    if (!newContent.trim()) return;
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
            type: activeTarget.type === 'direct' ? 'edit_private' : 'edit_group',
            id: id,
            content: newContent
        }));
        cancelEdit();
    } else {
        try {
            const url = activeTarget.type === 'direct' ? `/conversations/messages/${id}` : `/groups/messages/${id}`;
            const res = await authFetch(url, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ content: newContent })
            });
            if (!res.ok) {
                console.error("Edit REST failed:", await res.text());
            }
            cancelEdit();
        } catch (e) { 
            console.error("Edit REST error:", e);
            showToast('Failed to edit', 'error'); 
        }
    }
}

// ─── Message Rendering ────────────────────────────────────────────────────────
async function appendPrivateMessage(msg, prepend = false) {
  if (!msg || !msg.content) return;
  try {
    const isMe = msg.sender_username === me || msg.sender_id === -1;
    const time = formatTime(msg.created_at);
    const status = msg.status || 'sent';

    const html = `
        <div class="msg-row ${isMe ? 'outgoing' : 'incoming'}" id="msg-${msg.id}">
        <div class="msg-content-wrapper">
            <div class="bubble">
            ${msg.is_edited ? '<div class="edited-tag">(edited)</div>' : ''}
            ${msg.is_deleted ? '<span class="deleted-text">This message was deleted</span>' : `
                ${msg.reply_content ? `
                    <div class="reply-block" onclick="scrollToMessage(${msg.reply_to_id})">
                    <div class="reply-block-sender">${escHtml(msg.reply_sender || 'User')}</div>
                    <div class="reply-block-text">${escHtml(msg.reply_content)}</div>
                    </div>
                ` : ''}
                <div class="msg-text">${escHtml(msg.content)}</div>
            `}

            ${typeof renderReactions !== 'undefined' ? renderReactions(msg.reactions) : ''}
            </div>
            ${!msg.is_deleted ? `<button class="msg-dropdown-btn" title="Message options"
            onclick="openMsgCtxMenu(event,{id:${msg.id},content:'${escJs(msg.content)}',sender:'${escJs(msg.sender_username)}',isMe:${isMe},isGroup:false,isDeleted:${!!msg.is_deleted}})">
            <svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 10l5 5 5-5z"/></svg>
            </button>` : ''}
            <div class="bubble-meta">
            <span>${time}</span>
            ${isMe ? `<span class="status-tick ${status}">${statusIcon(status)}</span>` : ''}
            </div>
        </div>
        </div>`;
    insertMessage(html, prepend);
  } catch (e) {
    console.error("Error appending private message:", e, msg);
  }
}

async function appendGroupMessage(msg, prepend = false) {
  const isMe = msg.sender_username === me || msg.sender_id === -1;
  const time = formatTime(msg.created_at);
  const status = msg.status || 'sent';

  const html = `
    <div class="msg-row group-msg ${isMe ? 'outgoing' : 'incoming'}" id="msg-${msg.id}">
      <div class="msg-content-wrapper">
        ${!isMe ? `<div class="sender-label">${escHtml(msg.sender_username || '')}</div>` : ''}
        <div class="bubble">
          ${msg.is_edited ? '<div class="edited-tag">(edited)</div>' : ''}
          ${msg.is_deleted ? '<span class="deleted-text">This message was deleted</span>' : `
            ${msg.reply_content ? `
                <div class="reply-block" onclick="scrollToMessage(${msg.reply_to_id})">
                <div class="reply-block-sender">${escHtml(msg.reply_sender || 'User')}</div>
                <div class="reply-block-text">${escHtml(msg.reply_content)}</div>
                </div>
            ` : ''}
            <div class="msg-text">${escHtml(msg.content)}</div>
          `}

        ${renderReactions(msg.reactions)}
        </div>
        ${!msg.is_deleted ? `<button class="msg-dropdown-btn" title="Message options"
          onclick="openMsgCtxMenu(event,{id:${msg.id},content:'${escJs(msg.content)}',sender:'${escJs(msg.sender_username)}',isMe:${isMe},isGroup:true,isDeleted:${!!msg.is_deleted}})">
          <svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 10l5 5 5-5z"/></svg>
        </button>` : ''}
        <div class="bubble-meta">
          <span>${time}</span>
          ${isMe ? `<span class="status-tick ${status}">${statusIcon(status)}</span>` : ''}
        </div>
      </div>
    </div>`;
  insertMessage(html, prepend);
}

function insertMessage(html, prepend) {
  const container = document.getElementById('messagesContainer');
  const loadMoreBtn = document.getElementById('loadMoreBtn');
  
  let newEl;
  if (prepend) {
    loadMoreBtn.insertAdjacentHTML('afterend', html);
    newEl = loadMoreBtn.nextElementSibling;
  } else {
    container.insertAdjacentHTML('beforeend', html);
    newEl = container.lastElementChild;
  }
  
  if (newEl && newEl.classList.contains('incoming')) {
      readObserver.observe(newEl);
  }
}


// ─── Group Management ─────────────────────────────────────────────────────────
function openNewGroupModal() {
  document.getElementById('groupNameInput').value = '';
  document.getElementById('groupModal').classList.add('open');
}

async function createGroup() {
  const name = document.getElementById('groupNameInput').value.trim();
  if (!name) return;
  try {
    const res = await authFetch('/groups', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    if (!res.ok) { showToast('Failed to create group', 'error'); return; }
    const group = await res.json();
    allGroups.unshift(group);
    closeModal('groupModal');
    switchTab('groups');
    openGroupChat(group.id, group.name);
    showToast(`Group "${name}" created!`, 'success');
  } catch { showToast('Error creating group', 'error'); }
}

function openAddMemberModal(groupId) {
  document.getElementById('addMemberInput').value = '';
  document.getElementById('addMemberModal').dataset.groupId = groupId;
  document.getElementById('addMemberModal').classList.add('open');
}

async function addMember() {
  const modal = document.getElementById('addMemberModal');
  const groupId = parseInt(modal.dataset.groupId);
  const username = document.getElementById('addMemberInput').value.trim();
  if (!username) return;

  try {
    const res = await authFetch(`/groups/${groupId}/members`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username }),
    });
    if (!res.ok) {
      const err = await res.json();
      showToast(err.detail || 'Failed', 'error');
      return;
    }
    closeModal('addMemberModal');
    showToast(`${username} added!`, 'success');
  } catch { showToast('Error adding member', 'error'); }
}

async function showMembers() {
  if (!activeTarget || activeTarget.type !== 'group') return;
  const groupId = activeTarget.id;
  try {
    const res = await authFetch(`/groups/${groupId}/members`);
    if (!res.ok) return;
    const members = await res.json();
    const list = document.getElementById('membersList');
    list.innerHTML = members.map(m => `
      <div class="member-item">

        <div class="member-info">
          <span class="member-name">${escHtml(m.username)}</span>
          ${m.is_admin ? '<span class="member-role">Admin</span>' : ''}
        </div>
      </div>
    `).join('');
    document.getElementById('membersModal').classList.add('open');
  } catch { showToast('Failed to load members', 'error'); }
}

function closeModal(id) {
  document.getElementById(id).classList.remove('open');
}

// ─── Chat Options ────────────────────────────────────────────────────────────
function toggleChatOptions(e) {
  e.stopPropagation();
  const menu = document.getElementById('chatOptionsMenu');
  menu.classList.toggle('show');
}

// Close menu when clicking outside
document.addEventListener('click', () => {
  document.getElementById('chatOptionsMenu')?.classList.remove('show');
});

async function exitGroup() {
  if (!activeTarget || activeTarget.type !== 'group') return;
  if (!confirm('Are you sure you want to exit this group?')) return;
  
  try {
    const res = await authFetch(`/groups/${activeTarget.id}/leave`, { method: 'POST' });
    if (res.ok) {
      showToast('You left the group', 'info');
      await loadGroups();
      document.getElementById('activeChat').style.display = 'none';
      document.getElementById('placeholder').style.display = 'flex';
      activeTarget = null;
    } else {
      showToast('Failed to leave group', 'error');
    }
  } catch (e) { showToast('Error leaving group', 'error'); }
}

async function resolveGroup() {
  if (!activeTarget || activeTarget.type !== 'group') return;
  if (!confirm('WARNING: This will delete the group and all its messages for everyone. Proceed?')) return;
  
  try {
    const res = await authFetch(`/groups/${activeTarget.id}`, { method: 'DELETE' });
    if (res.ok) {
      showToast('Group resolved (deleted)', 'success');
      await loadGroups();
      document.getElementById('activeChat').style.display = 'none';
      document.getElementById('placeholder').style.display = 'flex';
      activeTarget = null;
    } else {
      showToast('Failed to resolve group', 'error');
    }
  } catch (e) { showToast('Error resolving group', 'error'); }
}

async function deleteChat() {
  if (!activeTarget) return;
  const confirmMsg = activeTarget.type === 'direct' 
    ? `Are you sure you want to delete all messages with ${activeTarget.username}?`
    : 'Are you sure you want to delete all messages in this group?';
    
  if (!confirm(confirmMsg)) return;
  
  try {
    let url = '';
    if (activeTarget.type === 'direct') {
      url = `/conversations/${activeTarget.username}`;
    } else {
      url = `/groups/${activeTarget.id}/messages`;
    }
    
    const res = await authFetch(url, { method: 'DELETE' });
    if (res.ok) {
      showToast('Chat history deleted', 'info');
      clearMessages();
      
      // Close the active chat view and show placeholder
      document.getElementById('activeChat').style.display = 'none';
      document.getElementById('placeholder').style.display = 'flex';
      activeTarget = null;
      
      if (activeTab === 'direct') {
        await loadActiveConversations();
      } else {
        await loadGroups();
      }
    } else {
      const err = await res.json().catch(() => ({}));
      showToast(err.detail || 'Failed to delete chat', 'error');
    }
  } catch (e) { 
    console.error("Delete chat error:", e);
    showToast('Error deleting chat', 'error'); 
  }
}

// ─── Helpers ──────────────────────────────────────────────────────────────────
function logout() {
  const refresh = getRefresh();
  if (refresh) {
    fetch('/auth/logout', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refresh }),
    }).catch(() => {});
  }
  localStorage.clear();
  window.location.href = '/login-page';
}

function handleEnter(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
}

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 140) + 'px';
}

function clearMessages() {
  const c = document.getElementById('messagesContainer');
  c.innerHTML = '<button class="load-more-btn" id="loadMoreBtn" onclick="loadMoreMessages()" style="display:none">Load earlier messages</button>';
}

function scrollToBottom() {
  const c = document.getElementById('messagesContainer');
  requestAnimationFrame(() => { c.scrollTop = c.scrollHeight; });
}

function updateContactPreview(username, text) {
  const el = document.getElementById(`preview-${username}`);
  if (el) el.textContent = text.slice(0, 40);
}

function formatTime(ts) {
  try {
    return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch { return ''; }
}

function statusIcon(status) {
  // Double tick SVG (WhatsApp-style)
  if (status === 'read') {
    return `<svg viewBox="0 0 18 11" fill="currentColor" xmlns="http://www.w3.org/2000/svg">
      <path d="M11.071.653l-1.386-.78-5.4 9.6-2.93-2.386-.867 1.066 4.18 3.404z" opacity=".6"/>
      <path d="M17.071.653l-1.386-.78-7.4 13.154-.042-.034-.035.062 1.386.78z"/>
    </svg>`;
  }
  if (status === 'delivered') {
    return `<svg viewBox="0 0 18 11" fill="currentColor" xmlns="http://www.w3.org/2000/svg">
      <path d="M11.071.653l-1.386-.78-5.4 9.6-2.93-2.386-.867 1.066 4.18 3.404z" opacity=".6"/>
      <path d="M17.071.653l-1.386-.78-7.4 13.154-.042-.034-.035.062 1.386.78z"/>
    </svg>`;
  }
  // sent — single tick
  return `<svg viewBox="0 0 10 11" fill="currentColor" xmlns="http://www.w3.org/2000/svg">
    <path d="M9.71.63L8.324-.15 2.924 9.45.003 7.064l-.867 1.066 4.18 3.404z"/>
  </svg>`;
}

function escHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/**
 * Escapes a string for use inside a single-quoted Javascript string literal
 * e.g. onclick="func('...')"
 */
function escJs(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/\\/g, '\\\\')
    .replace(/'/g, "\\'")
    .replace(/"/g, '\\"')
    .replace(/\n/g, '\\n')
    .replace(/\r/g, '\\r');
}

function showToast(msg, type = 'info') {
  const container = document.getElementById('toastContainer');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = msg;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 4000);
}

// Close modal on backdrop click
document.querySelectorAll('.modal-backdrop').forEach(backdrop => {
  backdrop.addEventListener('click', e => {
    if (e.target === backdrop) backdrop.classList.remove('open');
  });
});

function handleMessageReacted(data) {
  const el = document.getElementById(`msg-${data.id}`);
  if (!el) return;
  const bubble = el.querySelector('.bubble');
  if (!bubble) return;
  
  const existing = bubble.querySelector('.reactions-container');
  if (existing) existing.remove();
  
  if (!data.reactions || Object.keys(data.reactions).length === 0) return;
  
  const counts = {};
  for (const [usr, emoji] of Object.entries(data.reactions)) {
    counts[emoji] = (counts[emoji] || 0) + 1;
  }
  
  const reactionsHtml = Object.entries(counts).map(([emoji, count]) => `
    <span class="reaction-badge">${emoji} ${count > 1 ? count : ''}</span>
  `).join('');
  
  bubble.insertAdjacentHTML('beforeend', `<div class="reactions-container">${reactionsHtml}</div>`);
}


// --- Touch Long-Press Support ---
let touchTimer = null;
let touchTarget = null;

document.getElementById('messagesContainer').addEventListener('touchstart', (e) => {
  const bubble = e.target.closest('.bubble');
  if (!bubble) return;
  touchTarget = bubble;
  touchTimer = setTimeout(() => {
    const btn = bubble.parentElement.querySelector('.msg-dropdown-btn');
    if (btn) btn.click();
  }, 500);
}, { passive: true });

const clearTouch = () => { if (touchTimer) clearTimeout(touchTimer); };
document.getElementById('messagesContainer').addEventListener('touchend', clearTouch);
document.getElementById('messagesContainer').addEventListener('touchmove', clearTouch);
document.getElementById('messagesContainer').addEventListener('touchcancel', clearTouch);


function renderReactions(reactionsObj) {
  if (!reactionsObj || Object.keys(reactionsObj).length === 0) return '';
  const counts = {};
  for (const [usr, emoji] of Object.entries(reactionsObj)) {
    counts[emoji] = (counts[emoji] || 0) + 1;
  }
  const reactionsHtml = Object.entries(counts).map(([emoji, count]) => `
    <span class="reaction-badge">${emoji} ${count > 1 ? count : ''}</span>
  `).join('');
  return `<div class="reactions-container">${reactionsHtml}</div>`;
}
