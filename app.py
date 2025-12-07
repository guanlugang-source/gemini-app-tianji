import streamlit as st
import google.generativeai as genai
import pandas as pd
import requests
import json
from datetime import datetime, timedelta
import re

# ==========================================
# 🔑 配置区域 (完美适配你的 Secrets 设置)
# ==========================================
try:
    # 这里直接读取你在 Streamlit 网页后台填写的密码
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
    
    # 配置 Gemini
    genai.configure(api_key=GOOGLE_API_KEY)
except Exception as e:
    st.error("❌ 启动失败：找不到 API Key。")
    st.info("请确保在 Streamlit Cloud -> Advanced Settings -> Secrets 中填写了 GOOGLE_API_KEY")
    st.stop()

# ==========================================
# ⚙️ 策略核心参数
# ==========================================
STRATEGY = {
    "position_ratio": 0.16,
    "batch_split": 0.5,
    "add_buy_drop": 0.07,
    "stop_loss_from_avg": 0.07,
    "tp_main_board": 0.05,
    "tp_tech_board": 0.07,
    "trailing_drop": 0.08,
    "max_days": 20
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
        st.session_state.total_assets = 1000000.0
    if 'cash' not in st.session_state:
        st.session_state.cash = 1000000.0
    if 'active_trades' not in st.session_state:
        st.session_state.active_trades = []
    if 'history_trades' not in st.session_state:
        st.session_state.history_trades = []
    # 修复点：确保这里能引用到全局的 GOOGLE_API_KEY
    if 'api_key' not in st.session_state:
        st.session_state.api_key = GOOGLE_API_KEY

def get_stock_quote(code):
    """获取实时行情"""
    if not code or len(code) != 6:
        return None
    market = 'sh' if code[0] in ['5', '6', '9'] else 'sz'
    url = f"http://qt.gtimg.cn/q={market}{code}"
    try:
        response = requests.get(url, timeout=2)
        if response.status_code == 200 and f"v_{market}{code}=" in response.text:
            content = response.text.split('"')[1]
            parts = content.split('~')
            if len(parts) > 30:
                return {
                    'name': parts[1], 'code': parts[2], 'price': float(parts[3]),
                    'pct': float(parts[32]), 'vol': f"{float(parts[36])/10000:.1f}万"
                }
    except:
        pass
    return None

def get_board_type(code):
    if code.startswith(('688', '300', '4', '8')):
        return 'tech'
    return 'main'

def calculate_plan(total_capital, buy_price, code, name):
    board = get_board_type(code)
    single_limit = total_capital * STRATEGY['position_ratio']
    batch_money = single_limit * STRATEGY['batch_split']
    step1_shares = int(batch_money / buy_price // 100 * 100)
    if step1_shares == 0:
        return None, "资金不足买入一手"
    step1_cost = step1_shares * buy_price
    add_buy_price = buy_price * (1 - STRATEGY['add_buy_drop'])
    step2_shares = int(batch_money / add_buy_price // 100 * 100)
    total_shares = step1_shares + step2_shares
    avg_price = (step1_cost + (step2_shares * add_buy_price)) / total_shares
    tp_pct = STRATEGY['tp_tech_board'] if board == 'tech' else STRATEGY['tp_main_board']
    tp1_price = buy_price * (1 + tp_pct)
    stop_price = avg_price * (1 - STRATEGY['stop_loss_from_avg'])
    deadline = (datetime.now() + timedelta(days=STRATEGY['max_days'])).strftime('%Y-%m-%d')
    return {
        'code': code, 'name': name, 'board': board,
        'buy_price': buy_price, 'step1_shares': step1_shares, 'step1_money': step1_cost,
        'step2_price': add_buy_price, 'step2_shares': step2_shares,
        'avg_price': avg_price, 'tp1_price': tp1_price, 'tp_pct': tp_pct,
        'stop_price': stop_price, 'deadline': deadline,
        'date': datetime.now().strftime('%Y-%m-%d')
    }, None

def call_gemini(prompt):
    """调用 Gemini API"""
    # 这里使用 session_state 中已经存好的 Key
    api_key = st.session_state.api_key
    try:
        genai.configure(api_key=api_key)
        # 强制使用 Flash 模型（速度快且免费）
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"❌ AI 调用失败: {str(e)}"

# ==========================================
# 🎨 页面逻辑
# ==========================================
st.set_page_config(page_title="五行·天机 V21", page_icon="🛡️", layout="wide")
init_state()

# 侧边栏
with st.sidebar:
    st.title("🛡️ 五行·天机 V21")
    if st.button("🔄 重置数据"):
        st.session_state.clear()
        st.rerun()

# 顶部数据
mv = sum([t['step1_money'] for t in st.session_state.active_trades])
cols = st.columns(4)
cols[0].metric("总资产", f"¥ {st.session_state.cash + mv:,.0f}")
cols[1].metric("持仓市值", f"¥ {mv:,.0f}")
cols[2].metric("现金", f"¥ {st.session_state.cash:,.0f}")
wins = len([t for t in st.session_state.history_trades if t.get('profit', 0) > 0])
total = len(st.session_state.history_trades)
rate = (wins/total*100) if total > 0 else 0
cols[3].metric("胜率", f"{rate:.1f}%")

st.divider()
tab1, tab2, tab3, tab4 = st.tabs(["🚀 策略生成", "⚔️ 作战室", "🏛️ 档案馆", "🤖 AI 复盘"])

with tab1:
    c1, c2 = st.columns(2)
    with c1:
        code = st.text_input("代码 (6位)")
        price = st.number_input("价格", min_value=0.0)
        logic = st.selectbox("逻辑", list(REASONS.keys()), format_func=lambda x: REASONS[x]['label'])
        detail = st.text_area("理由")
        if st.button("✨ AI 验真"):
            if detail:
                with st.spinner("AI 分析中..."):
                    st.info(call_gemini(f"分析买入逻辑：{code} 价格{price} 理由{detail}。给出风险提示和止盈建议。"))
    with c2:
        if code and price > 0:
            plan, err = calculate_plan(st.session_state.total_assets, price, code, "未获取名称")
            if not err:
                st.success(f"策略已生成：首笔 {plan['step1_shares']} 股")
                if st.button("执行策略"):
                    if st.session_state.cash >= plan['step1_money']:
                        st.session_state.cash -= plan['step1_money']
                        plan['reason_type'] = logic
                        plan['reason_detail'] = detail
                        st.session_state.active_trades.insert(0, plan)
                        st.rerun()
                    else:
                        st.error("现金不足")

with tab2:
    for i, t in enumerate(st.session_state.active_trades):
        with st.expander(f"{t['code']} 持仓 {t['step1_shares']} 股"):
            st.write(f"止损价: {t['stop_price']:.2f}")
            profit = st.number_input(f"平仓盈亏 #{i}", key=f"p_{i}")
            if st.button(f"平仓 #{i}"):
                t['profit'] = profit
                st.session_state.cash += (t['step1_money'] + profit)
                st.session_state.history_trades.insert(0, t)
                st.session_state.active_trades.pop(i)
                st.rerun()

with tab3:
    if st.session_state.history_trades:
        st.dataframe(pd.DataFrame(st.session_state.history_trades))

with tab4:
    if st.button("全盘分析"):
        st.write(call_gemini(f"分析持仓风险：{st.session_state.active_trades}"))
