import streamlit as st
import google.generativeai as genai
import pandas as pd
import requests
import json
from datetime import datetime, timedelta
import re

# ==========================================
# 🔑 配置区域
# ==========================================
# 1. 获取 API Key (只写这一行就够了)
try:
    api_key = st.secrets["GOOGLE_API_KEY"]
    genai.configure(api_key=api_key)
except Exception as e:
    st.error("❌ 未找到 API Key，请在 Streamlit Cloud 的 Advanced Settings -> Secrets 中配置 GOOGLE_API_KEY")
    st.stop()

# ==========================================
# ⚙️ 策略核心参数
# ==========================================
STRATEGY = {
    "position_ratio": 0.16,      # 单票仓位上限 (16%)
    "batch_split": 0.5,          # 首笔仓位 (50%)
    "add_buy_drop": 0.07,        # 补仓跌幅 (-7%)
    "stop_loss_from_avg": 0.07,  # 综合成本止损 (-7%)
    "tp_main_board": 0.05,       # 主板首笔止盈 (+5%)
    "tp_tech_board": 0.07,       # 双创板首笔止盈 (+7%)
    "trailing_drop": 0.08,       # 移动止损回撤 (8%)
    "max_days": 20               # 持仓大限 (天)
}

REASONS = {
    'tech': {'label': '技术形态', 'icon': '📈', 'hint': '均线多头、量价配合、MACD金叉、突破压力位'},
    'fund': {'label': '基本面', 'icon': '💰', 'hint': 'PE/PB低估、业绩超预期、高股息、行业拐点'},
    'event': {'label': '事件驱动', 'icon': '📢', 'hint': '并购重组、政策利好、产品涨价、大订单'},
    'sector': {'label': '板块情绪', 'icon': '🔥', 'hint': '板块涨停潮、龙头连板、高标反馈、主力净流入'}
}

# ==========================================
# 🛠️ 辅助函数
# ==========================================

def init_state():
    """初始化 Session State"""
    if 'total_assets' not in st.session_state:
        st.session_state.total_assets = 1000000.0  # 默认100万
    if 'cash' not in st.session_state:
        st.session_state.cash = 1000000.0
    if 'active_trades' not in st.session_state:
        st.session_state.active_trades = []
    if 'history_trades' not in st.session_state:
        st.session_state.history_trades = []
    if 'api_key' not in st.session_state:
        st.session_state.api_key = GOOGLE_API_KEY

def get_stock_quote(code):
    """获取实时行情 (腾讯接口)"""
    if not code or len(code) != 6:
        return None
    
    market = 'sh' if code[0] in ['5', '6', '9'] else 'sz'
    url = f"http://qt.gtimg.cn/q={market}{code}"
    
    try:
        response = requests.get(url, timeout=2)
        if response.status_code == 200:
            data = response.text
            if f"v_{market}{code}=" in data and len(data) > 50:
                # 解析数据: v_sh600519="1~贵州茅台~600519~1530.00~..."
                content = data.split('"')[1]
                parts = content.split('~')
                return {
                    'name': parts[1],
                    'code': parts[2],
                    'price': float(parts[3]),
                    'pct': float(parts[32]),
                    'high': parts[33],
                    'low': parts[34],
                    'open': parts[5],
                    'vol': f"{float(parts[36])/10000:.1f}万"
                }
    except Exception as e:
        st.error(f"行情获取失败: {e}")
    return None

def get_board_type(code):
    """判断板块"""
    if code.startswith(('688', '300', '4', '8')):
        return 'tech'
    return 'main'

def calculate_plan(total_capital, buy_price, code, name):
    """生成交易计划"""
    board = get_board_type(code)
    
    single_limit = total_capital * STRATEGY['position_ratio']
    batch_money = single_limit * STRATEGY['batch_split']
    
    # 向下取整到100股
    step1_shares = int(batch_money / buy_price // 100 * 100)
    if step1_shares == 0:
        return None, "资金不足买入一手"
        
    step1_cost = step1_shares * buy_price
    
    add_buy_price = buy_price * (1 - STRATEGY['add_buy_drop'])
    step2_shares = int(batch_money / add_buy_price // 100 * 100)
    
    # 模拟补仓后的数据
    total_shares = step1_shares + step2_shares
    avg_price = (step1_cost + (step2_shares * add_buy_price)) / total_shares
    
    # 止盈止损
    tp_pct = STRATEGY['tp_tech_board'] if board == 'tech' else STRATEGY['tp_main_board']
    tp1_price = buy_price * (1 + tp_pct) # 基于当前买入价
    post_add_tp1 = avg_price * (1 + tp_pct) # 补仓后的止盈
    stop_price = avg_price * (1 - STRATEGY['stop_loss_from_avg'])
    
    # 时间
    deadline = (datetime.now() + timedelta(days=28)).strftime('%Y-%m-%d')
    
    return {
        'code': code, 'name': name, 'board': board,
        'buy_price': buy_price,
        'step1_shares': step1_shares, 'step1_money': step1_cost,
        'step2_price': add_buy_price, 'step2_shares': step2_shares,
        'avg_price': avg_price,
        'tp1_price': tp1_price, 'tp_pct': tp_pct,
        'post_add_tp1': post_add_tp1,
        'stop_price': stop_price,
        'deadline': deadline,
        'date': datetime.now().strftime('%Y-%m-%d')
    }, None

def call_gemini(prompt):
    """调用 Gemini API"""
    api_key = st.session_state.api_key.strip()
    if not api_key:
        return "⚠️ 请先在左侧栏配置 Google API Key"
    
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"❌ AI 调用失败: {str(e)}"

# ==========================================
# 🎨 页面布局
# ==========================================

st.set_page_config(page_title="五行·天机 V21 Python版", page_icon="🛡️", layout="wide")

# 自定义 CSS 样式
st.markdown("""
<style>
    .metric-card {
        background-color: #f8f9fa;
        border: 1px solid #e9ecef;
        border-radius: 10px;
        padding: 15px;
        text-align: center;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    }
    .metric-label { font-size: 12px; color: #6c757d; font-weight: bold; text-transform: uppercase; }
    .metric-value { font-size: 24px; font-weight: bold; color: #212529; font-family: 'Consolas', monospace; }
    .profit-up { color: #dc3545 !important; }
    .profit-down { color: #28a745 !important; }
    
    div[data-testid="stExpander"] { border: 1px solid #e0e0e0; border-radius: 8px; }
    
    /* 侧边栏样式微调 */
    section[data-testid="stSidebar"] { background-color: #fcfcfc; }
</style>
""", unsafe_allow_html=True)

init_state()

# --- 侧边栏：设置 ---
with st.sidebar:
    st.title("🛡️ 五行·天机 V21")
    st.caption("AI 驱动的量化资管系统")
    
    with st.expander("🔑 API 配置", expanded=not bool(st.session_state.api_key)):
        new_key = st.text_input("Google Gemini API Key", value=st.session_state.api_key, type="password")
        if new_key != st.session_state.api_key:
            st.session_state.api_key = new_key
            st.success("API Key 已更新")
    
    st.markdown("---")
    
    # 资金重置
    if st.button("🔄 重置所有数据"):
        st.session_state.total_assets = 1000000.0
        st.session_state.cash = 1000000.0
        st.session_state.active_trades = []
        st.session_state.history_trades = []
        st.rerun()
        
    st.markdown("### 📜 兵法摘要")
    st.info("""
    1. **分仓**：5只票，单票16%，现金20%。
    2. **建仓**：首笔50%，跌7%补50%。
    3. **止盈**：首笔+5%/+7%卖1/3，余下移动止盈。
    4. **止损**：成本-7%或20日未盈利。
    """)

# --- 顶部：资金驾驶舱 ---
def calculate_market_value():
    # 简单估算：使用持仓成本代替市值（实盘应接入实时价格）
    return sum([t['step1_money'] for t in st.session_state.active_trades])

market_value = calculate_market_value()
# 动态更新总资产 (Cash + MV)
# 注意：这里简化处理，实际总资产应随市值波动。这里我们保持 Cash 准确，Total Asset 显示当前状态。
current_total = st.session_state.cash + market_value

col1, col2, col3, col4 = st.columns(4)

with col1:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">资金总量 (Total Assets)</div>
        <div class="metric-value">¥ {current_total:,.0f}</div>
    </div>
    """, unsafe_allow_html=True)

with col2:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">市值总值 (Market Value)</div>
        <div class="metric-value" style="color:#0d6efd">¥ {market_value:,.0f}</div>
    </div>
    """, unsafe_allow_html=True)

with col3:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">可用现金 (Cash)</div>
        <div class="metric-value" style="color:#198754">¥ {st.session_state.cash:,.0f}</div>
    </div>
    """, unsafe_allow_html=True)

with col4:
    # 胜率计算
    wins = len([t for t in st.session_state.history_trades if t['profit'] > 0])
    total_h = len(st.session_state.history_trades)
    win_rate = (wins / total_h * 100) if total_h > 0 else 0
    win_color = "#dc3545" if win_rate > 50 else "#28a745"
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">历史胜率 (Win Rate)</div>
        <div class="metric-value" style="color:{win_color}">{win_rate:.1f}%</div>
    </div>
    """, unsafe_allow_html=True)

st.markdown("---")

# --- 主界面：Tab 分页 ---
tab1, tab2, tab3, tab4 = st.tabs(["🚀 策略生成 (天眼)", "⚔️ 作战室 (持仓)", "🏛️ 档案馆 (历史)", "🤖 AI 参谋长"])

# ==========================================
# Tab 1: 策略生成
# ==========================================
with tab1:
    col_input, col_preview = st.columns([1, 1])
    
    with col_input:
        st.subheader("1. 标的输入")
        code_input = st.text_input("股票代码 (6位)", max_chars=6, placeholder="例如 600519")
        
        current_price = 0.0
        stock_name = ""
        
        if code_input and len(code_input) == 6:
            quote = get_stock_quote(code_input)
            if quote:
                stock_name = quote['name']
                current_price = quote['price']
                pct = quote['pct']
                color = "red" if pct >= 0 else "green"
                st.markdown(f"**{quote['name']}** : <span style='color:{color};font-size:1.2em'>{quote['price']}</span> ({pct}%)", unsafe_allow_html=True)
            else:
                st.warning("未找到股票信息")

        buy_price = st.number_input("拟买入价格", value=current_price, min_value=0.0, format="%.2f")
        
        st.subheader("2. 天眼矩阵 (逻辑锁定)")
        logic_type = st.selectbox("核心主导逻辑", list(REASONS.keys()), format_func=lambda x: f"{REASONS[x]['icon']} {REASONS[x]['label']}")
        
        st.caption(f"💡 提示: {REASONS[logic_type]['hint']}")
        reason_detail = st.text_area("详细买入理由 (AI将进行压测)", height=100, placeholder="请详细描述逻辑，例如：突破60日均线，量能放大...")
        
        if st.button("✨ AI 逻辑验真", type="secondary", use_container_width=True):
            if not reason_detail:
                st.warning("请填写买入理由")
            else:
                with st.spinner("天眼系统正在全维扫描..."):
                    prompt = f"""
                    你是一个专业A股交易员。用户计划买入 {stock_name}({code_input}) 价格{buy_price}。
                    核心逻辑：{REASONS[logic_type]['label']}。
                    详细理由：{reason_detail}。
                    
                    请用中文(200字以内)进行逻辑压测：
                    1. **风险提示**：指出该逻辑最大的潜在风险点。
                    2. **止盈建议**：根据该逻辑属性（短线情绪或长线价值），给出具体的止盈思路。
                    3. **结论**：批准执行 / 需再观察。
                    """
                    ai_reply = call_gemini(prompt)
                    st.info(ai_reply)

    with col_preview:
        if buy_price > 0 and code_input:
            plan, error = calculate_plan(st.session_state.total_assets, buy_price, code_input, stock_name)
            
            if error:
                st.error(error)
            else:
                st.subheader("3. 策略预览")
                
                p_card = st.container()
                p_card.markdown(f"""
                #### 🎯 {stock_name} ({code_input})
                **板块**: {'科创/创业' if plan['board']=='tech' else '主板'} | **大限**: {plan['deadline']}
                """, unsafe_allow_html=True)
                
                c1, c2 = p_card.columns(2)
                c1.metric("1. 底仓 (50%)", f"¥ {plan['buy_price']}", f"{plan['step1_shares']} 股", delta_color="off")
                c2.metric("2. 补仓 (-7%)", f"¥ {plan['step2_price']:.2f}", f"{plan['step2_shares']} 股", delta_color="inverse")
                
                st.divider()
                
                c3, c4 = p_card.columns(2)
                c3.metric("🎯 首笔止盈", f"¥ {plan['tp1_price']:.2f}", f"+{plan['tp_pct']*100:.0f}%")
                c4.metric("🛡️ 极限止损", f"¥ {plan['stop_price']:.2f}", "综合成本 -7%", delta_color="inverse")
                
                st.caption(f"预计占用现金: ¥ {plan['step1_money']:,.0f} (总本金的 {(plan['step1_money']/st.session_state.total_assets)*100:.1f}%)")

                if st.button("🚀 确认执行 (加入作战室)", type="primary", use_container_width=True):
                    if plan['step1_money'] > st.session_state.cash:
                        st.error("现金不足！")
                    else:
                        st.session_state.cash -= plan['step1_money']
                        # 记录完整逻辑
                        plan['reason_type'] = logic_type
                        plan['reason_detail'] = reason_detail
                        plan['cost'] = plan['step1_money'] # 初始成本
                        st.session_state.active_trades.insert(0, plan)
                        st.success(f"{stock_name} 已加入作战室！")
                        st.rerun()

# ==========================================
# Tab 2: 作战室 (持仓)
# ==========================================
with tab2:
    if not st.session_state.active_trades:
        st.empty()
        st.info("作战室空空如也，请去制定策略。")
    
    for i, trade in enumerate(st.session_state.active_trades):
        with st.expander(f"{trade['name']} ({trade['code']}) - 成本 {trade['buy_price']}", expanded=True):
            cols = st.columns([2, 2, 3])
            cols[0].write(f"**持仓**: {trade['step1_shares']} 股")
            cols[1].write(f"**大限**: {trade['deadline']}")
            cols[2].caption(f"逻辑: {REASONS[trade['reason_type']]['label']}")
            
            st.markdown(f"""
            - 🎯 **止盈目标**: `{trade['tp1_price']:.2f}` (触价卖出 1/3)
            - 🛡️ **止损红线**: `{trade['stop_price']:.2f}` (跌破清仓)
            - 🛒 **补仓挂单**: `{trade['step2_price']:.2f}` (买入 {trade['step2_shares']} 股)
            """)
            
            st.divider()
            
            c_act1, c_act2 = st.columns(2)
            with c_act1:
                # 简单平仓逻辑
                close_profit = st.number_input(f"平仓盈亏 (元) #{i}", step=100.0, key=f"profit_{i}")
            with c_act2:
                st.write("")
                st.write("")
                if st.button(f"🏁 平仓结算 #{i}"):
                    # 归档
                    trade['profit'] = close_profit
                    trade['close_date'] = datetime.now().strftime('%Y-%m-%d')
                    
                    # 资金回笼 (本金 + 盈亏)
                    st.session_state.cash += (trade['cost'] + close_profit)
                    st.session_state.total_assets = st.session_state.cash + calculate_market_value() - trade['cost'] # 更新总资产
                    
                    st.session_state.history_trades.insert(0, trade)
                    st.session_state.active_trades.pop(i)
                    st.success("交易已归档！")
                    st.rerun()

# ==========================================
# Tab 3: 档案馆 (历史)
# ==========================================
with tab3:
    if st.session_state.history_trades:
        df = pd.DataFrame(st.session_state.history_trades)
        # 简单处理显示
        display_df = df[['date', 'close_date', 'code', 'name', 'reason_type', 'profit']].copy()
        display_df.columns = ['买入日期', '平仓日期', '代码', '名称', '逻辑', '盈亏']
        display_df['逻辑'] = display_df['逻辑'].map(lambda x: REASONS.get(x, {}).get('label', x))
        
        # 样式化盈亏
        st.dataframe(display_df.style.applymap(lambda x: 'color: red' if x > 0 else 'color: green', subset=['盈亏']), use_container_width=True)
        
        total_pl = display_df['盈亏'].sum()
        color = "red" if total_pl > 0 else "green"
        st.markdown(f"### 历史总盈亏: <span style='color:{color}'>¥ {total_pl:,.2f}</span>", unsafe_allow_html=True)
        
        # CSV 下载
        csv = display_df.to_csv(index=False).encode('utf-8-sig')
        st.download_button("📥 导出 CSV", csv, "history.csv", "text/csv")
    else:
        st.info("暂无历史交易记录")

# ==========================================
# Tab 4: AI 参谋长
# ==========================================
with tab4:
    st.subheader("🧠 AI 全局风控与复盘")
    
    col_a, col_b = st.columns(2)
    
    with col_a:
        if st.button("🛡️ 持仓全局体检", use_container_width=True):
            if not st.session_state.active_trades:
                st.warning("暂无持仓可分析")
            else:
                with st.spinner("AI 参谋长正在扫描全盘风险..."):
                    holdings_str = ", ".join([f"{t['name']}({REASONS[t['reason_type']]['label']})" for t in st.session_state.active_trades])
                    prompt = f"""
                    我是投资经理。目前持仓：{holdings_str}。
                    请分析该组合的风险敞口（行业集中度、风格重叠度）。
                    请用中文，200字以内，给出调仓或风控建议。
                    """
                    res = call_gemini(prompt)
                    st.success("分析完成")
                    st.markdown(res)

    with col_b:
        if st.button("📊 历史战绩复盘", use_container_width=True):
            if not st.session_state.history_trades:
                st.warning("暂无历史数据")
            else:
                with st.spinner("AI 正在分析您的交易习惯..."):
                    history_str = ", ".join([f"{t['name']}(盈亏{t['profit']},逻辑{REASONS[t['reason_type']]['label']})" for t in st.session_state.history_trades])
                    prompt = f"""
                    根据以下A股交易记录生成复盘报告：{history_str}。
                    请分析该交易员在不同逻辑（技术/基本面等）下的胜率表现。
                    指出他最擅长的模式和最容易亏钱的模式。
                    200字以内，中文。
                    """
                    res = call_gemini(prompt)
                    st.success("复盘报告")
                    st.markdown(res)
