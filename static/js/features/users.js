// ==================== 用户管理 ====================

let _usersPageRole = null; // 当前登录用户角色（由模板注入后赋值）

function initUsersPage(role) {
    _usersPageRole = role;
}

async function loadUsersPage() {
    await loadMyInfo();
    if (_usersPageRole === 'admin') {
        await loadUsersList();
    }
}

// ── 当前用户信息 ──────────────────────────────────────────────────────────────

async function loadMyInfo() {
    try {
        const res = await fetch('/api/users/me');
        const data = await res.json();
        if (data.success && data.user) {
            const u = data.user;
            const el = document.getElementById('myInfoDisplay');
            if (el) {
                el.innerHTML = `
                    <span style="font-weight:600;">${escapeHtml(u.username)}</span>
                    <span class="badge-role ${u.role === 'admin' ? 'badge-admin' : 'badge-user'}">${u.role === 'admin' ? '管理员' : '普通用户'}</span>
                    <span style="color:var(--text-muted);font-size:0.82rem;">创建于 ${u.created_at ? u.created_at.slice(0,10) : '--'}</span>
                `;
            }
        }
    } catch (e) {
        console.error('loadMyInfo error', e);
    }
}

async function changeMyPassword() {
    const newPwd = document.getElementById('myNewPassword').value.trim();
    const confirmPwd = document.getElementById('myConfirmPassword').value.trim();
    if (!newPwd) { showToast('请输入新密码', 'warn'); return; }
    if (newPwd.length < 8) { showToast('密码长度至少 8 位', 'warn'); return; }
    if (newPwd !== confirmPwd) { showToast('两次密码不一致', 'warn'); return; }

    const btn = document.getElementById('btnChangeMyPwd');
    btn.disabled = true;
    try {
        const res = await fetch('/api/users/me/password', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ new_password: newPwd })
        });
        const data = await res.json();
        if (data.success) {
            showToast('密码已修改，下次登录生效', 'success');
            document.getElementById('myNewPassword').value = '';
            document.getElementById('myConfirmPassword').value = '';
        } else {
            showToast(data.error?.message || '修改失败', 'error');
        }
    } catch (e) {
        showToast('请求失败: ' + e.message, 'error');
    } finally {
        btn.disabled = false;
    }
}

// ── 用户列表（管理员） ─────────────────────────────────────────────────────────

async function loadUsersList() {
    const container = document.getElementById('usersTableBody');
    if (!container) return;
    container.innerHTML = '<tr><td colspan="4" style="text-align:center;padding:1.5rem;color:var(--text-muted);">加载中…</td></tr>';
    try {
        const res = await fetch('/api/users');
        const data = await res.json();
        if (!data.success) { showToast(data.error?.message || '加载失败', 'error'); return; }
        renderUsersList(data.users || []);
    } catch (e) {
        showToast('请求失败: ' + e.message, 'error');
    }
}

function renderUsersList(users) {
    const container = document.getElementById('usersTableBody');
    if (!container) return;
    if (!users.length) {
        container.innerHTML = '<tr><td colspan="4" style="text-align:center;padding:1.5rem;color:var(--text-muted);">暂无用户</td></tr>';
        return;
    }
    container.innerHTML = users.map(u => `
        <tr>
            <td style="padding:0.7rem 1rem;font-weight:500;">${escapeHtml(u.username)}</td>
            <td style="padding:0.7rem 1rem;">
                <span class="badge-role ${u.role === 'admin' ? 'badge-admin' : 'badge-user'}">${u.role === 'admin' ? '管理员' : '普通用户'}</span>
            </td>
            <td style="padding:0.7rem 1rem;color:var(--text-muted);font-size:0.85rem;">${u.created_at ? u.created_at.slice(0,10) : '--'}</td>
            <td style="padding:0.7rem 1rem;">
                <div style="display:flex;gap:6px;flex-wrap:wrap;">
                    <button class="btn btn-sm btn-outline" onclick="showResetPasswordModal(${u.id}, '${escapeHtml(u.username)}')">重置密码</button>
                    <button class="btn btn-sm btn-outline" onclick="toggleUserRole(${u.id}, '${u.role}', '${escapeHtml(u.username)}')">
                        ${u.role === 'admin' ? '改为普通用户' : '改为管理员'}
                    </button>
                    <button class="btn btn-sm btn-danger" onclick="deleteUser(${u.id}, '${escapeHtml(u.username)}')">删除</button>
                </div>
            </td>
        </tr>
    `).join('');
}

// ── 创建用户 ──────────────────────────────────────────────────────────────────

async function createUser() {
    const username = document.getElementById('newUsername').value.trim();
    const password = document.getElementById('newUserPassword').value.trim();
    const role = document.getElementById('newUserRole').value;

    if (!username) { showToast('用户名不能为空', 'warn'); return; }
    if (password.length < 8) { showToast('密码长度至少 8 位', 'warn'); return; }

    const btn = document.getElementById('btnCreateUser');
    btn.disabled = true;
    try {
        const res = await fetch('/api/users', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password, role })
        });
        const data = await res.json();
        if (data.success) {
            showToast(`用户 "${username}" 已创建`, 'success');
            document.getElementById('newUsername').value = '';
            document.getElementById('newUserPassword').value = '';
            document.getElementById('newUserRole').value = 'user';
            await loadUsersList();
        } else {
            showToast(data.error?.message || '创建失败', 'error');
        }
    } catch (e) {
        showToast('请求失败: ' + e.message, 'error');
    } finally {
        btn.disabled = false;
    }
}

// ── 删除用户 ──────────────────────────────────────────────────────────────────

async function deleteUser(userId, username) {
    if (!confirm(`确定要删除用户 "${username}" 吗？\n该用户的所有邮箱账号、分组、标签数据都将被删除，且无法恢复。`)) return;
    try {
        const res = await fetch(`/api/users/${userId}`, { method: 'DELETE' });
        const data = await res.json();
        if (data.success) {
            showToast(`用户 "${username}" 已删除`, 'success');
            await loadUsersList();
        } else {
            showToast(data.error?.message || '删除失败', 'error');
        }
    } catch (e) {
        showToast('请求失败: ' + e.message, 'error');
    }
}

// ── 修改用户角色 ───────────────────────────────────────────────────────────────

async function toggleUserRole(userId, currentRole, username) {
    const newRole = currentRole === 'admin' ? 'user' : 'admin';
    const label = newRole === 'admin' ? '管理员' : '普通用户';
    if (!confirm(`确定将用户 "${username}" 的角色改为 "${label}" 吗？`)) return;
    try {
        const res = await fetch(`/api/users/${userId}/role`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ role: newRole })
        });
        const data = await res.json();
        if (data.success) {
            showToast(`已将 "${username}" 改为 ${label}`, 'success');
            await loadUsersList();
        } else {
            showToast(data.error?.message || '更新失败', 'error');
        }
    } catch (e) {
        showToast('请求失败: ' + e.message, 'error');
    }
}

// ── 重置密码 Modal ─────────────────────────────────────────────────────────────

function showResetPasswordModal(userId, username) {
    document.getElementById('resetPwdUserId').value = userId;
    document.getElementById('resetPwdUsername').textContent = username;
    document.getElementById('resetPwdNewPassword').value = '';
    document.getElementById('modalResetPassword').style.display = 'flex';
}

function closeResetPasswordModal() {
    document.getElementById('modalResetPassword').style.display = 'none';
}

async function submitResetPassword() {
    const userId = document.getElementById('resetPwdUserId').value;
    const username = document.getElementById('resetPwdUsername').textContent;
    const newPwd = document.getElementById('resetPwdNewPassword').value.trim();
    if (newPwd.length < 8) { showToast('密码长度至少 8 位', 'warn'); return; }

    const btn = document.getElementById('btnSubmitResetPwd');
    btn.disabled = true;
    try {
        const res = await fetch(`/api/users/${userId}/password`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ new_password: newPwd })
        });
        const data = await res.json();
        if (data.success) {
            showToast(`用户 "${username}" 密码已重置`, 'success');
            closeResetPasswordModal();
        } else {
            showToast(data.error?.message || '重置失败', 'error');
        }
    } catch (e) {
        showToast('请求失败: ' + e.message, 'error');
    } finally {
        btn.disabled = false;
    }
}

// ── 工具 ──────────────────────────────────────────────────────────────────────

function escapeHtml(str) {
    return String(str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
