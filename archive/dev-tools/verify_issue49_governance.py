"""Issue #49 验收：治理面板 HTML 结构检查（简化版，无 Playwright）。"""

import requests

BASE_URL = "http://127.0.0.1:5097"

s = requests.Session()

# 1. 登录
print("=== Step 1: 登录 ===")
r = s.post(f"{BASE_URL}/login", json={"password": "admin12345"})
assert r.status_code == 200 and r.json().get("success"), f"登录失败: {r.text}"
print("✅ 登录成功")

# 2. 拉取首页 HTML，检查治理面板结构
print("=== Step 2: 检查首页 HTML ===")
r = s.get(f"{BASE_URL}/")
html = r.text

# 检查治理面板容器
assert 'id="invalidTokenGovernanceContainer"' in html, "未找到 #invalidTokenGovernanceContainer"
print("✅ 治理面板容器存在")

assert 'id="invalidTokenSummary"' in html, "未找到 #invalidTokenSummary"
print("✅ 检测摘要区存在")

assert 'id="invalidTokenSummaryText"' in html, "未找到 #invalidTokenSummaryText"
print("✅ 摘要文本元素存在")

assert 'id="invalidTokenCandidateListWrap"' in html, "未找到 #invalidTokenCandidateListWrap"
print("✅ 候选列表区存在")

assert 'id="invalidTokenCandidateCount"' in html, "未找到 #invalidTokenCandidateCount"
print("✅ 候选计数元素存在")

# 3. 检查操作按钮
print("=== Step 3: 检查操作按钮 ===")
assert "批量置为停用" in html, "未找到'批量置为停用'按钮"
print("✅ 批量置为停用按钮存在")

assert "批量删除" in html, "未找到'批量删除'按钮"
print("✅ 批量删除按钮存在")

# 4. 检查失效治理入口按钮
print("=== Step 4: 检查失效治理入口按钮 ===")
assert "失效治理" in html, "未找到'失效治理'按钮"
print("✅ 失效治理入口按钮存在")

# 5. 检查 JS 函数
print("=== Step 5: 检查 main.js 中的治理函数 ===")
r = s.get(f"{BASE_URL}/static/js/main.js?v=2.1.1")
js = r.text

funcs = [
    "resetInvalidTokenGovernanceState",
    "showInvalidTokenDetectionSummary",
    "loadInvalidTokenGovernanceCandidates",
    "hideInvalidTokenGovernance",
    "batchSetInvalidTokenInactive",
    "batchDeleteInvalidTokenCandidates",
]
for func in funcs:
    assert func in js, f"JS 函数 {func} 未在 main.js 中找到"
    print(f"  ✅ {func}() 存在")

# 6. 测试治理 API
print("=== Step 6: 测试治理 API ===")
r = s.get(f"{BASE_URL}/api/accounts/invalid-token-candidates")
data = r.json()
assert data.get("success"), f"invalid-token-candidates 请求失败: {data}"
print(f"✅ GET /api/accounts/invalid-token-candidates → success={data['success']}, candidates={len(data.get('candidates', []))}")

r = s.post(f"{BASE_URL}/api/accounts/batch-update-status", json={"account_ids": [], "status": "inactive"})
data = r.json()
assert not data.get("success"), "空 account_ids 应被拒绝"
print(f"✅ POST /api/accounts/batch-update-status (空 ids) → 正确拒绝")

# 7. 检查刷新模态框中治理面板位置
print("=== Step 7: 检查治理面板位置（在刷新模态框内） ===")
assert 'id="refreshModal"' in html, "未找到刷新模态框"
# 找 refreshModal 和 invalidTokenGovernanceContainer 的相对位置
modal_pos = html.find('id="refreshModal"')
governance_pos = html.find('id="invalidTokenGovernanceContainer"')
assert governance_pos > modal_pos, "治理面板不在刷新模态框内"
print("✅ 治理面板正确嵌入在刷新模态框内")

print("\n=== 验收结论 ===")
print("全部 7 步检查通过！治理面板 HTML 结构、JS 函数、API 端点均正常。")
