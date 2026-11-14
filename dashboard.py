import streamlit as st
import pandas as pd
import plotly.express as px
import os
import itertools
import uuid
import json
import requests
import time
import re
from datetime import datetime, timedelta
import streamlit.components.v1 as components
from sqlalchemy import create_engine, text
from streamlit_autorefresh import st_autorefresh

# ================= 🎨 页面配置与完美 UI CSS =================
st.set_page_config(page_title="数据量化终端", layout="wide", page_icon="📊")

st.markdown("""
    <style>
    header[data-testid="stHeader"] { display: none !important; }
    .block-container { padding-top: 0.5rem !important; padding-bottom: 0.5rem !important; padding-left: 1rem !important; padding-right: 1rem !important; }
    .stApp { background-color: #0A0A0A; color: #E5E5E5; }
    .custom-card { background-color: #121212; border: 1px solid #262626; border-radius: 6px; padding: 15px; margin-bottom: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }
    h1, h2, h3 { color: #FFFFFF !important; font-family: -apple-system, sans-serif; margin-bottom: 0px !important; }
    label { color: #A3A3A3 !important; font-size: 12px !important; padding-bottom: 0px !important; }
    [data-testid="stSidebar"] { background-color: #0F0F0F; border-right: 1px solid #262626; }
    
    .ball-container { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
    .ball { display: inline-flex; align-items: center; justify-content: center; width: 32px; height: 32px; border-radius: 50%; font-weight: bold; font-size: 14px; box-shadow: 0 2px 4px rgba(0,0,0,0.3); }
    .ball-red { background-color: #7F1D1D; color: #FCA5A5; border: 1px solid #EF4444; }
    .ball-blue { background-color: #1E3A8A; color: #93C5FD; border: 1px solid #3B82F6; }
    .ball-green { background-color: #14532D; color: #86EFAC; border: 1px solid #22C55E; }
    .ball-special { box-shadow: 0 0 8px 1px rgba(255, 255, 255, 0.4); transform: scale(1.05); font-weight: 900; }
    
    .huge-number { font-size: 36px; font-weight: 800; line-height: 1.1; }
    .profit { color: #22C55E; }
    .loss { color: #EF4444; }
    .text-gray { color: #A3A3A3; font-size: 13px; }
    .peak-text-pos { color: #22C55E; font-size: 13px; font-weight: bold; margin-top: 5px; }
    .peak-text-neg { color: #EF4444; font-size: 13px; font-weight: bold; margin-top: 3px; }
    </style>
""", unsafe_allow_html=True)

# ================= 🗄️ 云端数据库配置 =================
try:
    DB_URL = st.secrets["DB_URL"]
except:
    DB_URL = "postgresql+psycopg2://postgres.ishhmayoclgbnwdwojim:PPOOIIpoipoi8899@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres"

@st.cache_resource
def get_db_engine():
    return create_engine(DB_URL)

engine = get_db_engine()

# ================= 🎱 六合彩波色字典 =================
RED_BALLS = {1, 2, 7, 8, 12, 13, 18, 19, 23, 24, 29, 30, 34, 35, 40, 45, 46}
BLUE_BALLS = {3, 4, 9, 10, 14, 15, 20, 25, 26, 31, 36, 37, 41, 42, 47, 48}
GREEN_BALLS = {5, 6, 11, 16, 17, 21, 22, 27, 28, 32, 33, 38, 39, 43, 44, 49}

def get_ball_class(num, is_special=False):
    base_class = ""
    if num in RED_BALLS: base_class = "ball-red"
    elif num in BLUE_BALLS: base_class = "ball-blue"
    elif num in GREEN_BALLS: base_class = "ball-green"
    if is_special: return f"ball {base_class} ball-special"
    return f"ball {base_class}"

def play_beep_sound():
    js = """
    <script>
    function playSound() {
        var context = new (window.AudioContext || window.webkitAudioContext)();
        var osc = context.createOscillator(); var gain = context.createGain();
        osc.connect(gain); gain.connect(context.destination);
        osc.type = 'sine'; osc.frequency.setValueAtTime(880, context.currentTime);
        gain.gain.setValueAtTime(0.5, context.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, context.currentTime + 0.8);
        osc.start(); osc.stop(context.currentTime + 0.8);
    }
    playSound();
    </script>
    """
    components.html(js, height=0, width=0)

# ================= 🔄 数据同步逻辑 =================
LOTTERY_MAP = {
    "香港六合彩": {"code": "HK6", "type": "dpc"},
    "快乐8六合彩": {"code": "KLLHC", "type": "dpc"},
    "宾果六合彩": {"code": "TWBGLHC", "type": "gpc"},
    "加拿大六合彩": {"code": "JNDLHC", "type": "gpc"}
}

def force_sync_until_today(lottery_name):
    """智能断档补齐：自动检测本地库最新日期，补齐到今天"""
    config = LOTTERY_MAP.get(lottery_name)
    if not config: return "未知彩种"

    end_date = datetime.today().date()

    # 1. 查库里最新的一期是哪天
    try:
        df_old = pd.read_sql(f"SELECT draw_time FROM lottery_data WHERE lottery_name = '{lottery_name}' ORDER BY draw_time DESC LIMIT 1", engine)
        if df_old.empty:
            start_date = end_date - timedelta(days=30)  # 空库默认抓30天
        else:
            max_date = pd.to_datetime(df_old.iloc[0]['draw_time']).date()
            # ✅ 只有 香港六合彩 / 快乐8六合彩 才跳过"日期短路"，并回扫 max_date - 1 天
            #    其他彩种（含高频彩）走原逻辑，行为 100% 不变
            if lottery_name in ("香港六合彩", "快乐8六合彩"):
                start_date = max_date - timedelta(days=1)
            else:
                if max_date >= end_date:
                    return "✅ 已是最新 (本地已有今天的开奖数据)"
                start_date = max_date
    except Exception as e:
        return f"❌ 读取数据库失败: {e}"

    delta_days = (end_date - start_date).days + 1

    # 💡 安全阀：为了防止断档半年后点击导致请求爆炸被封IP，最多往回抓60天
    if delta_days > 60:
        start_date = end_date - timedelta(days=60)
        delta_days = 61
        limit_msg = " (因断档过久，为防封禁仅补齐最近60天)"
    else:
        limit_msg = ""

    all_new_data = []
    error_logs = []
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://166w2.com/"}

    for day_offset in range(delta_days):
        target_date = start_date + timedelta(days=day_offset)
        y, m, d = target_date.strftime("%Y"), target_date.month, target_date.day

        if config["type"] == "dpc":
            url = f"https://data.kai8.us/api/lottery/history_dpc/KAI166/{config['code']}/{y}/{m}/{d}"
            try:
                res = requests.get(url, headers=headers, timeout=5)
                if res.status_code == 200:
                    for item in res.json().get("data", {}).get("list", []):
                        all_new_data.append({"lottery_name": lottery_name, "issue": str(item.get("issue")).strip(), "draw_time": item.get("draw_time"), "draw_code": item.get("draw_code")})
                else:
                    error_logs.append(f"{target_date} HTTP:{res.status_code}")
            except Exception as e:
                error_logs.append(f"{target_date} 异常:{str(e)[:20]}")
            time.sleep(0.02)
        else:
            # 高频彩（宾果/加拿大）分支：原样保留，一行不改
            for h in range(24):
                url = f"https://data.kai8.us/api/lottery/history_gpc/KAI166/{config['code']}/{y}-{m:02d}-{d:02d}/{h}/30"
                try:
                    res = requests.get(url, headers=headers, timeout=5)
                    if res.status_code == 200:
                        for item in res.json().get("data", {}).get("list", []):
                            all_new_data.append({"lottery_name": lottery_name, "issue": str(item.get("issue")).strip(), "draw_time": item.get("draw_time"), "draw_code": item.get("draw_code")})
                except Exception: pass
                time.sleep(0.01)

    if not all_new_data:
        err_msg = "; ".join(error_logs[:3]) if error_logs else "接口未返回任何数据(可能官方还没更新)"
        return f"⚠️ 抓取失败: {err_msg}"

    df_new = pd.DataFrame(all_new_data)
    df_new['issue'] = df_new['issue'].astype(str).str.strip()
    df_new['draw_time'] = pd.to_datetime(df_new['draw_time'], errors='coerce').dt.tz_localize(None)
    df_new = df_new.dropna(subset=['draw_time']).drop_duplicates(subset=['lottery_name', 'issue'], keep='first')

    try:
        existing = pd.read_sql(f"SELECT issue FROM lottery_data WHERE lottery_name = '{lottery_name}'", engine)['issue'].astype(str).str.strip().tolist()
        df_new = df_new[~df_new['issue'].isin(existing)]

        # 接口本次返回的最新期号（用于给"已是最新"补上真实期号，避免误判）
        api_latest_issue = pd.DataFrame(all_new_data)['issue'].astype(str).str.strip().max()

        if not df_new.empty:
            df_new.to_sql('lottery_data', engine, if_exists='append', index=False)
            return f"✅ 补齐完成！扫描了 {start_date} 至 {end_date}，新增 {len(df_new)} 条数据。最新一期: {df_new.iloc[-1]['issue']}{limit_msg}"
        else:
            # ✅ 区分：接口有数据但全已存在 vs 接口本次没返回
            return f"✅ 已是最新（接口最新期 {api_latest_issue} 已入库，扫描 {start_date} 至 {end_date}）{limit_msg}"
    except Exception as e:
        return f"❌ 写入数据库失败: {e}"

def auto_sync_data(lottery_name):
    """后台静默增量同步（仅针对最新一天，用于高频彩自动刷新）"""
    config = LOTTERY_MAP.get(lottery_name)
    if not config: return False
    end_date = datetime.today().date()
    try:
        df_old = pd.read_sql(f"SELECT draw_time FROM lottery_data WHERE lottery_name = '{lottery_name}' ORDER BY draw_time DESC LIMIT 1", engine)
        start_date = end_date - timedelta(days=3) if df_old.empty else pd.to_datetime(df_old.iloc[0]['draw_time']).date()
    except Exception: return False

    if start_date > end_date: return False
    delta_days = (end_date - start_date).days + 1
    if delta_days > 30: start_date, delta_days = end_date - timedelta(days=30), 31
        
    all_new_data = []
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://166w2.com/"}
    for day_offset in range(delta_days):
        target_date = start_date + timedelta(days=day_offset)
        y, m, d = target_date.strftime("%Y"), target_date.month, target_date.day
        if config["type"] == "dpc":
            url = f"https://data.kai8.us/api/lottery/history_dpc/KAI166/{config['code']}/{y}/{m}/{d}"
            try:
                res = requests.get(url, headers=headers, timeout=5)
                if res.status_code == 200:
                    for item in res.json().get("data", {}).get("list", []):
                        all_new_data.append({"lottery_name": lottery_name, "issue": str(item.get("issue")).strip(), "draw_time": item.get("draw_time"), "draw_code": item.get("draw_code")})
            except Exception: pass
            time.sleep(0.02)
        else:
            for h in range(24):
                url = f"https://data.kai8.us/api/lottery/history_gpc/KAI166/{config['code']}/{y}-{m:02d}-{d:02d}/{h}/30"
                try:
                    res = requests.get(url, headers=headers, timeout=5)
                    if res.status_code == 200:
                        for item in res.json().get("data", {}).get("list", []):
                            all_new_data.append({"lottery_name": lottery_name, "issue": str(item.get("issue")).strip(), "draw_time": item.get("draw_time"), "draw_code": item.get("draw_code")})
                except Exception: pass
                time.sleep(0.01)
    if not all_new_data: return False
    
    df_new = pd.DataFrame(all_new_data)
    df_new['issue'] = df_new['issue'].astype(str).str.strip()
    df_new['draw_time'] = pd.to_datetime(df_new['draw_time'], errors='coerce').dt.tz_localize(None)
    df_new = df_new.dropna(subset=['draw_time']).drop_duplicates(subset=['lottery_name', 'issue'], keep='first')

    try:
        existing = pd.read_sql(f"SELECT issue FROM lottery_data WHERE lottery_name = '{lottery_name}'", engine)['issue'].astype(str).str.strip().tolist()
        df_new = df_new[~df_new['issue'].isin(existing)]
        if not df_new.empty:
            df_new.to_sql('lottery_data', engine, if_exists='append', index=False)
            return True
    except Exception: pass
    return False

# ================= 💾 方案云端保存 =================
def save_scheme_to_db(scheme):
    try:
        df = pd.DataFrame([{"id": scheme["id"], "scheme_json": json.dumps(scheme)}])
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM saved_schemes WHERE id = :id"), {"id": scheme["id"]})
            df.to_sql('saved_schemes', engine, if_exists='append', index=False)
        st.toast(f"方案 '{scheme['name']}' 已保存至云端", icon="✅")
    except Exception as e: 
        st.error(f"方案保存云端失败: {e}")

def delete_scheme_from_db(scheme_id):
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM saved_schemes WHERE id = :id"), {"id": scheme_id})
        st.toast("方案已从云端删除", icon="🗑️")
    except Exception as e: 
        st.error(f"方案删除失败: {e}")

def load_schemes():
    try:
        df = pd.read_sql("SELECT scheme_json FROM saved_schemes", engine)
        if df.empty: return []
        schemes = []
        for row in df['scheme_json']:
            if isinstance(row, dict): schemes.append(row)
            elif isinstance(row, str): schemes.append(json.loads(row))
        return schemes
    except Exception as e:
        st.error(f"🚨 读取方案失败，请检查云端数据库连接！ {e}")
        return []

if 'schemes' not in st.session_state: 
    st.session_state.schemes = load_schemes() 
if 'editing_id' not in st.session_state: 
    st.session_state.editing_id = None
if 'sync_msg' not in st.session_state:
    st.session_state.sync_msg = None

# URL 记忆
if "chart_height" not in st.session_state: st.session_state.chart_height = int(st.query_params.get("chart_height", 350))
if "dtick_val" not in st.session_state: st.session_state.dtick_val = int(st.query_params.get("dtick_val", 10000))
if "pos_alert" not in st.session_state: st.session_state.pos_alert = float(st.query_params.get("pos_alert", 0.0))
if "neg_alert" not in st.session_state: st.session_state.neg_alert = float(st.query_params.get("neg_alert", 0.0))

def sync_alerts_to_url():
    st.query_params["pos_alert"] = str(st.session_state.pos_alert)
    st.query_params["neg_alert"] = str(st.session_state.neg_alert)

# ================= 1. 数据解析 =================
available_lotteries = list(LOTTERY_MAP.keys())

def parse_draw(row):
    code_str = str(row["draw_code"]) if "draw_code" in row else str(row["开奖号码"])
    nums = [int(x) for x in code_str.replace('"', '').replace("'", "").split(",") if x.strip().isdigit()]
    if len(nums) >= 7: return sorted(nums[:6]), nums[-1]
    return [], 0

# ================= 2. 侧边栏控制台 =================
st.sidebar.markdown("### 全局控制")
st.sidebar.markdown("**📅 快捷时间**")

import calendar
today = datetime.today().date()

this_week_start = today - timedelta(days=today.weekday())
last_week_start = this_week_start - timedelta(days=7)
last_week_end = this_week_start - timedelta(days=1)
this_month_start = today.replace(day=1)
last_month_end = this_month_start - timedelta(days=1)
last_month_start = last_month_end.replace(day=1)
this_year_start = today.replace(month=1, day=1)

if 'start_date_widget' not in st.session_state:
    st.session_state.start_date_widget = this_month_start
if 'end_date_widget' not in st.session_state:
    st.session_state.end_date_widget = today

def set_dates(s_date, e_date):
    st.session_state.start_date_widget = s_date
    st.session_state.end_date_widget = e_date

c1, c2, c3 = st.sidebar.columns(3)
if c1.button("今天", use_container_width=True): set_dates(today, today)
if c2.button("本周", use_container_width=True): set_dates(this_week_start, today)
if c3.button("上周", use_container_width=True): set_dates(last_week_start, last_week_end)

c4, c5, c6 = st.sidebar.columns(3)
if c4.button("本月", use_container_width=True): set_dates(this_month_start, today)
if c5.button("上月", use_container_width=True): set_dates(last_month_start, last_month_end)
if c6.button("今年", use_container_width=True): set_dates(this_year_start, today)

start_date = st.sidebar.date_input("开始日期", key="start_date_widget")
end_date = st.sidebar.date_input("结束日期", key="end_date_widget")

st.sidebar.markdown("---")
st.sidebar.markdown("### 方案管理")

is_editing = st.session_state.editing_id is not None
edit_scheme = next((s for s in st.session_state.schemes if s['id'] == st.session_state.editing_id), None) if is_editing else None
form_title = "修改方案" if is_editing else "新增方案"

with st.sidebar.expander(form_title, expanded=is_editing):
    if is_editing: st.info(f"正在修改：{edit_scheme['name']}")

    def_name = edit_scheme['name'] if is_editing else f"方案 {len(st.session_state.schemes) + 1}"
    def_group = edit_scheme.get('group_name', '') if is_editing else ""
    def_cat = edit_scheme['category'] if is_editing else "连码复式"
    def_sub = edit_scheme['sub_type'] if is_editing else "3中3"
    def_nums = " ".join(map(str, edit_scheme['numbers'])) if is_editing else "4 5 9 14 17 37 41 42 47"
    def_nums2 = " ".join(map(str, edit_scheme['numbers2'])) if is_editing and edit_scheme.get('numbers2') else ""
    def_pos = edit_scheme['pos_index'] if is_editing else 1
    def_bet = edit_scheme['bet'] if is_editing else 10
    def_rebate = edit_scheme['rebate'] if is_editing else 0.155

    scheme_name = st.text_input("方案名称", value=def_name)
    group_name = st.text_input("所属组合 (可选)", value=def_group, placeholder="比如：544大方案")
    category = st.selectbox("玩法类目", ["连码复式", "连码对碰", "特码类"], index=["连码复式", "连码对碰", "特码类"].index(def_cat))
    
    if is_editing:
        odds_val_1 = float(edit_scheme.get('odds_1') or 700.0)
        odds_val_2 = float(edit_scheme.get('odds_2') or 125.0)
    else:
        odds_val_1, odds_val_2 = 700.0, 125.0

    if category == "特码类":
        sub_cat = st.selectbox("玩法细分", ["特码", "正码特"], index=["特码", "正码特"].index(def_sub) if def_sub in ["特码", "正码特"] else 0)
        pos_index = st.selectbox("指定位置 (1-6正码，7特码)", [1,2,3,4,5,6,7], index=def_pos-1)
        numbers_str = st.text_input("号码池", value=def_nums)
        numbers2_str = ""
        odds_val_1 = st.number_input("手动设置赔率", value=odds_val_1, step=1.0)
        odds_val_2 = None
    elif category == "连码复式":
        sub_cat = st.selectbox("玩法细分", ["3中3", "3中2", "2中2", "特串"], index=["3中3", "3中2", "2中2", "特串"].index(def_sub) if def_sub in ["3中3", "3中2", "2中2", "特串"] else 0)
        numbers_str = st.text_input("号码池", value=def_nums)
        
        if sub_cat == "特串":
            numbers2_str = "" 
        else:
            numbers2_str = ""
            
        if sub_cat == "3中2":
            col1, col2 = st.columns(2)
            with col1: odds_val_1 = st.number_input("中2个赔率", value=odds_val_1, step=1.0)
            with col2: odds_val_2 = st.number_input("中3个赔率", value=odds_val_2, step=1.0)
        else:
            default_odds = 700.0 if sub_cat == "3中3" else (65.8 if sub_cat == "2中2" else 165.0)
            odds_val_1 = st.number_input("手动设置赔率", value=odds_val_1 if is_editing else default_odds, step=1.0)
            odds_val_2 = None
        pos_index = 0
    else: 
        sub_cat = st.selectbox("玩法细分", ["3中3", "3中2", "2中2", "特串"], index=["3中3", "3中2", "2中2", "特串"].index(def_sub) if def_sub in ["3中3", "3中2", "2中2", "特串"] else 0)
        numbers_str = st.text_input("左边号码组 (组A)", value=def_nums)
        numbers2_str = st.text_input("右边号码组 (组B)", value=def_nums2)
        if sub_cat == "3中2":
            col1, col2 = st.columns(2)
            with col1: odds_val_1 = st.number_input("中2个赔率", value=odds_val_1, step=1.0)
            with col2: odds_val_2 = st.number_input("中3个赔率", value=odds_val_2, step=1.0)
        else:
            default_odds = 700.0 if sub_cat == "3中3" else (65.8 if sub_cat == "2中2" else 165.0)
            odds_val_1 = st.number_input("手动设置赔率", value=odds_val_1 if is_editing else default_odds, step=1.0)
            odds_val_2 = None
        pos_index = 0

    bet_amount = st.number_input("单注金额", value=def_bet, step=1)
    rebate = st.number_input("退水比例", value=def_rebate, step=0.005, format="%.3f")

    btn_text = "保存修改" if is_editing else "添加方案"
    if st.button(btn_text, use_container_width=True, type="primary"):
        try:
            num_pool = [int(x) for x in re.findall(r'\d+', str(numbers_str))]
            num_pool2 = [int(x) for x in re.findall(r'\d+', str(numbers2_str))] if numbers2_str else []
            
            new_scheme = {
                "id": edit_scheme['id'] if is_editing else str(uuid.uuid4()),
                "name": scheme_name, "group_name": group_name, "category": category, 
                "sub_type": sub_cat, "pos_index": pos_index, "numbers": num_pool, "numbers2": num_pool2, 
                "bet": bet_amount, "rebate": rebate, "odds_1": odds_val_1, "odds_2": odds_val_2, 
                "active": edit_scheme['active'] if is_editing else True
            }
            if is_editing:
                idx = next(i for i, s in enumerate(st.session_state.schemes) if s['id'] == st.session_state.editing_id)
                st.session_state.schemes[idx] = new_scheme
                st.session_state.editing_id = None
            else:
                st.session_state.schemes.append(new_scheme)
            
            save_scheme_to_db(new_scheme) 
            st.rerun()
        except Exception as e: 
            st.error(f"操作失败: {e}")
    if is_editing:
        if st.button("取消修改", use_container_width=True): 
            st.session_state.editing_id = None
            st.rerun()

active_schemes = []
def render_scheme_row(s):
    c1, c2, c3 = st.columns([3, 1, 1])
    is_active = c1.checkbox(s['name'], value=s['active'], key=f"chk_{s['id']}")
    if is_active != s['active']: 
        s['active'] = is_active
        save_scheme_to_db(s)
    if c2.button("改", key=f"e_{s['id']}"): st.session_state.editing_id = s['id']; st.rerun()
    if c3.button("删", key=f"d_{s['id']}"):
        st.session_state.schemes = [x for x in st.session_state.schemes if x['id'] != s['id']]
        delete_scheme_from_db(s['id']) 
        st.rerun()
    if s['active']: active_schemes.append(s)

if st.session_state.schemes:
    with st.sidebar.expander("已保存的方案列表", expanded=True):
        groups = {}
        for s in st.session_state.schemes:
            g_name = s.get('group_name', '').strip() or "（未分组）"
            groups.setdefault(g_name, []).append(s)
            
        for g_name, s_list in groups.items():
            if g_name == "（未分组）":
                for s in s_list: render_scheme_row(s)
            else:
                group_active = all(s['active'] for s in s_list)
                new_group_active = st.checkbox(f"组合: {g_name}", value=group_active, key=f"grp_{g_name}")
                if new_group_active != group_active:
                    for s in s_list: 
                        s['active'] = new_group_active
                        save_scheme_to_db(s)
                    st.rerun()
                with st.expander(f"查看 {g_name} 内的方案", expanded=False):
                    for s in s_list: render_scheme_row(s)

# ================= 3. 主界面：控制台 =================
st.markdown(f"<h2 style='font-size: 20px; display:inline-block;'>历史盈亏回测</h2> <span style='font-size:12px;color:#A3A3A3;margin-left:10px;'>范围: {start_date} 至 {end_date}</span>", unsafe_allow_html=True)

c_lottery, c_action = st.columns([1.5, 2.5])
with c_lottery:
    selected_lottery = st.selectbox("目标彩种", available_lotteries)
    st.query_params["lottery"] = selected_lottery 
    
    is_high_freq = ("宾果" in selected_lottery or "加拿大" in selected_lottery)
    
    # 💡 临时注释掉自动同步进行排查，如果页面出来了说明是它导致的
    if is_high_freq:
        st_autorefresh(interval=60000, limit=None, key="high_freq_refresh")
        # auto_sync_data(selected_lottery) 
    else:
        cache_key = f"synced_{selected_lottery}"
        if cache_key not in st.session_state:
            # auto_sync_data(selected_lottery)
            st.session_state[cache_key] = True

    try:
        query = f"SELECT issue as 期号, draw_time as 开奖日期, draw_code as 开奖号码 FROM lottery_data WHERE lottery_name = '{selected_lottery}'"
        df_all = pd.read_sql(query, engine)
        df_all["开奖日期"] = pd.to_datetime(df_all["开奖日期"], errors='coerce').dt.tz_localize(None)
        df_all["日期_only"] = df_all["开奖日期"].dt.date
        df_all = df_all.dropna(subset=["开奖日期"]).sort_values(by="期号", ascending=True)
    except Exception as e:
        st.error(f"读取图表数据失败: {e}")
        df_all = pd.DataFrame()

with c_action:
    st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)
    if st.button("立即手动补齐断档数据", use_container_width=True, type="primary"):
        msg = force_sync_until_today(selected_lottery)
        st.session_state.sync_msg = msg
        st.rerun()

if st.session_state.sync_msg:
    if "✅" in st.session_state.sync_msg:
        st.success(st.session_state.sync_msg)
    elif "⚠️" in st.session_state.sync_msg or "❌" in st.session_state.sync_msg:
        st.error(st.session_state.sync_msg)
    else:
        st.info(st.session_state.sync_msg)
    st.session_state.sync_msg = None

if not df_all.empty:
    latest_row = df_all.iloc[-1]
    latest_issue, latest_date = latest_row['期号'], latest_row['开奖日期'].strftime('%Y-%m-%d %H:%M')
    latest_normals, latest_special = parse_draw(latest_row)
    
    if latest_normals:
        balls_html = "".join([f"<div class='{get_ball_class(n)}'>{n:02d}</div>" for n in latest_normals])
        balls_html += f"<div class='{get_ball_class(latest_special, True)}'>{latest_special:02d}</div>"
        st.markdown(f"""
        <div style="background-color: #121212; border: 1px solid #262626; border-radius: 6px; padding: 8px 12px; margin-top: 5px; margin-bottom: 10px; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px;">
            <div><span style="color: #FFF; font-weight: bold; font-size: 14px;">第 {latest_issue} 期</span><span style="color: #666; font-size: 12px; margin-left: 10px;">{latest_date}</span></div>
            <div class="ball-container">{balls_html}</div>
        </div>
        """, unsafe_allow_html=True)

with st.expander("报警线设置与图表调节", expanded=False):
    st.caption("💡 填 0 或留空代表不设提醒。将根据最新一期累计盈亏是否突破该值来报警。")
    c_alert1, c_alert2 = st.columns(2)
    with c_alert1: st.number_input("正峰值报警线 (元) - 止盈", step=1000.0, key="pos_alert", on_change=sync_alerts_to_url)
    with c_alert2: st.number_input("负峰值报警线 (元) - 止损", step=-1000.0, key="neg_alert", on_change=sync_alerts_to_url)
    
    c_height, c_dtick = st.columns(2)
    with c_height:
        new_height = st.slider("图表高度", min_value=250, max_value=900, value=st.session_state.chart_height, step=50)
        if new_height != st.session_state.chart_height:
            st.session_state.chart_height = new_height
            st.query_params["chart_height"] = str(new_height)
            st.rerun()
            
    with c_dtick:
        new_dtick = st.number_input("坐标轴主单位 (Y轴)", min_value=100, max_value=1000000, value=st.session_state.dtick_val, step=1000)
        if new_dtick != st.session_state.dtick_val:
            st.session_state.dtick_val = new_dtick
            st.query_params["dtick_val"] = str(new_dtick)
            st.rerun()

@st.cache_data(show_spinner=False)
def calculate_scheme_cached(df, scheme, time_filter=""):
    cat, sub = scheme['category'], scheme['sub_type']
    if cat == "特码类": bet_count = len(scheme['numbers'])
    elif cat == "连码复式":
        n = len(scheme['numbers'])
        if sub in ["3中3", "3中2"]: bet_count = len(list(itertools.combinations(range(n), 3)))
        elif sub == "2中2": bet_count = len(list(itertools.combinations(range(n), 2)))
        else: bet_count = len(list(itertools.combinations(range(n), 2)))
    else: 
        L, R = len(scheme['numbers']), len(scheme['numbers2'])
        if sub in ["2中2", "特串"]: bet_count = L * R
        else: bet_count = int(L*(L-1)/2)*R + L*int(R*(R-1)/2)
    actual_cost = bet_count * scheme['bet'] * (1 - scheme['rebate'])

    results = []
    for _, row in df.iterrows():
        if time_filter and time_filter not in row['开奖日期'].strftime('%H:%M'): continue
        draw_normals, draw_special = set(row['正码']), row['特码']
        h2, h3, hn = 0, 0, 0
        
        if cat == "特码类":
            pos_idx = scheme['pos_index'] - 1
            draw_num = row['正码'][pos_idx] if pos_idx < 6 else row['特码']
            if draw_num in scheme['numbers']: hn = scheme['numbers'].count(draw_num)
        elif cat == "连码复式":
            nums = scheme['numbers']
            if sub in ["3中3", "3中2"]:
                for grp in itertools.combinations(nums, 3):
                    match = sum(1 for n in grp if n in draw_normals)
                    if sub == "3中3" and match == 3: hn += 1
                    elif sub == "3中2":
                        if match == 2: h2 += 1
                        elif match == 3: h3 += 1
            elif sub == "2中2":
                for grp in itertools.combinations(nums, 2):
                    if all(n in draw_normals for n in grp): hn += 1
            elif sub == "特串":
                for grp in itertools.combinations(nums, 2):
                    n1, n2 = grp
                    if (n1 in draw_normals and n2 == draw_special) or (n2 in draw_normals and n1 == draw_special):
                        hn += 1
        elif cat == "连码对碰":
            L_nums, R_nums = scheme['numbers'], scheme['numbers2']
            if sub == "2中2":
                for l in L_nums:
                    for r in R_nums:
                        if l in draw_normals and r in draw_normals: hn += 1
            elif sub == "特串":
                for l in L_nums:
                    for r in R_nums:
                        if (l in draw_normals and r == draw_special) or (l == draw_special and r in draw_normals): hn += 1
            elif sub in ["3中3", "3中2"]:
                for l_pair in itertools.combinations(L_nums, 2):
                    for r in R_nums:
                        match = sum(1 for n in (list(l_pair) + [r]) if n in draw_normals)
                        if sub == "3中3" and match == 3: hn += 1
                        elif sub == "3中2":
                            if match == 2: h2 += 1
                            elif match == 3: h3 += 1
                for l in L_nums:
                    for r_pair in itertools.combinations(R_nums, 2):
                        match = sum(1 for n in ([l] + list(r_pair)) if n in draw_normals)
                        if sub == "3中3" and match == 3: hn += 1
                        elif sub == "3中2":
                            if match == 2: h2 += 1
                            elif match == 3: h3 += 1

        prize = (h2 * scheme['bet'] * scheme['odds_1'] + h3 * scheme['bet'] * scheme['odds_2']) if sub == "3中2" else (hn * scheme['bet'] * scheme['odds_1'])
        pl = prize - actual_cost
        total_hit = h2 + h3 if sub == "3中2" else hn
        results.append({"期号": row['期号'], "开奖日期": row['开奖日期'], "正码": row['正码'], "特码": draw_special, "命中注数": total_hit, "本期盈亏": round(pl, 2)})
        
    res_df = pd.DataFrame(results)
    if not res_df.empty: res_df['累计盈亏'] = res_df['本期盈亏'].cumsum()
    return res_df

# ================= 5. 测算结果展示 =================
if not active_schemes:
    st.info("请在左侧勾选至少一个方案以展示测算结果。")
else:
    df_filtered = df_all[(df_all["日期_only"] >= start_date) & (df_all["日期_only"] <= end_date)].copy()
    if df_filtered.empty:
        st.warning("当前日期范围内云端没有数据！请调整左侧时间范围。")
        st.stop()
        
    df_filtered["正码"], df_filtered["特码"] = zip(*df_filtered.apply(parse_draw, axis=1))
    
    total_schemes = len(active_schemes)
    total_days = len(df_filtered)
    progress_text = f"正在计算历史盈亏... (共 {total_days} 期数据)"
    my_bar = st.progress(0, text=progress_text)
    
    scheme_results = {}
    all_res_dfs = []
    
    for i, scheme in enumerate(active_schemes):
        res = calculate_scheme_cached(df_filtered, scheme, "")
        scheme_results[scheme['id']] = res
        if not res.empty:
            res['方案名称'] = scheme['name']
            all_res_dfs.append(res)
        pct = int((i + 1) / total_schemes * 100)
        my_bar.progress((i + 1) / total_schemes, text=f"正在计算方案: {scheme['name']} ({pct}%) | 已处理 {total_days} 期")
        
    my_bar.empty()
    
    if not all_res_dfs:
        st.warning("当前过滤条件下无数据。")
        st.stop()
        
    combined_df = pd.concat(all_res_dfs)
    merged_pl = combined_df.groupby(['期号', '开奖日期']).agg({'命中注数': 'sum', '本期盈亏': 'sum'}).reset_index()
    merged_pl['累计盈亏'] = merged_pl['本期盈亏'].cumsum()
    total_combined_pl = merged_pl['本期盈亏'].sum()

    peak_value = merged_pl['累计盈亏'].max()
    peak_issue = merged_pl.loc[merged_pl['累计盈亏'].idxmax(), '期号']
    low_peak_value = merged_pl['累计盈亏'].min()
    low_peak_issue = merged_pl.loc[merged_pl['累计盈亏'].idxmin(), '期号']
    
    latest_pl = merged_pl.iloc[-1]['累计盈亏']
    
    alert_pos_val = st.session_state.pos_alert
    alert_neg_val = st.session_state.neg_alert
    
    trigger_pos = (alert_pos_val != 0) and (latest_pl >= alert_pos_val)
    trigger_neg = (alert_neg_val != 0) and (latest_pl <= alert_neg_val)
    
    if trigger_pos or trigger_neg:
        play_beep_sound()
        if trigger_pos and trigger_neg:
            st.error(f"🚨 **极度波动报警！** 最新累计盈亏 ¥{latest_pl:,.2f} 同时突破了止盈(¥{alert_pos_val:,.2f})和止损(¥{alert_neg_val:,.2f})线。")
        elif trigger_pos:
            st.success(f"🎉 **止盈报警！** 最新累计盈亏 ¥{latest_pl:,.2f} 突破正峰值报警线 (设定线: ¥{alert_pos_val:,.2f})")
        elif trigger_neg:
            st.warning(f"⚠️ **止损报警！** 最新累计盈亏 ¥{latest_pl:,.2f} 跌破负峰值报警线 (设定线: ¥{alert_neg_val:,.2f})")

    active_groups = set([s.get('group_name','').strip() for s in active_schemes if s.get('group_name','').strip()])
    combined_names = list(active_groups)[0] if active_groups and len(active_groups) == 1 else " + ".join([s['name'] for s in active_schemes])
    
    st.markdown(f"### 合并总盈亏 ({combined_names})")
    
    c_mid, c_right = st.columns([1.5, 3])
    with c_mid:
        st.markdown(f"""
        <div class="custom-card" style="border-left: 4px solid {'#22C55E' if total_combined_pl >= 0 else '#EF4444'}; padding: 15px; background: #121212; border-radius: 6px;">
            <div class="text-gray" style="color: #A3A3A3; font-size: 13px;">合计盈亏 (元)</div>
            <div class="huge-number" style="font-size: 36px; font-weight: 800; color: {'#22C55E' if total_combined_pl >= 0 else '#EF4444'};">¥ {total_combined_pl:,.2f}</div>
            <div class="peak-text-pos" style="color: #22C55E; font-size: 13px; margin-top: 5px;">最高: ¥ {peak_value:,.2f} ({peak_issue}期)</div>
            <div class="peak-text-neg" style="color: #EF4444; font-size: 13px; margin-top: 3px;">最低: ¥ {low_peak_value:,.2f} ({low_peak_issue}期)</div>
        </div>
        """, unsafe_allow_html=True)
    
    with c_right:
        fig_combined = px.line(merged_pl, x="期号", y="累计盈亏", markers=True, template="plotly_dark")
        fig_combined.update_layout(
            plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', 
            height=st.session_state.chart_height, margin=dict(l=10, r=10, t=25, b=5), 
            title=dict(text="合并总盈亏走势", font=dict(size=12, color="#A3A3A3")), 
            yaxis=dict(dtick=st.session_state.dtick_val)
        )
        fig_combined.add_scatter(x=[peak_issue], y=[peak_value], mode='markers+text', text=[f"最高: {peak_value:,.0f}"], textposition="top center", marker=dict(size=8, color='#22C55E', symbol='star'), textfont=dict(color='#22C55E', size=10))
        fig_combined.add_scatter(x=[low_peak_issue], y=[low_peak_value], mode='markers+text', text=[f"最低: {low_peak_value:,.0f}"], textposition="bottom center", marker=dict(size=8, color='#EF4444', symbol='star'), textfont=dict(color='#EF4444', size=10))
        fig_combined.update_traces(selector=dict(type='scatter', mode='lines+markers'), line_color='#38BDF8', line_width=2, marker=dict(size=5, color='#38BDF8'))
        st.plotly_chart(fig_combined, use_container_width=True, key="chart_combined")

    with st.expander("查看合并每日明细记录", expanded=False):
        st.dataframe(merged_pl, use_container_width=True, hide_index=True, height=200)
    
    st.markdown("---")
    st.markdown("### 各方案独立明细")
    
    tabs = st.tabs([s['name'] for s in active_schemes])
    for idx, scheme in enumerate(active_schemes):
        with tabs[idx]:
            res_df = scheme_results.get(scheme['id'])
            if res_df is None or res_df.empty: continue
            
            total_pl = res_df['本期盈亏'].sum()
            win_periods = len(res_df[res_df['命中注数'] > 0])
            win_rate = (win_periods / len(res_df) * 100) if len(res_df) > 0 else 0
            
            s_peak_value = res_df['累计盈亏'].max()
            s_peak_issue = res_df.loc[res_df['累计盈亏'].idxmax(), '期号']
            s_low_peak_value = res_df['累计盈亏'].min()
            s_low_peak_issue = res_df.loc[res_df['累计盈亏'].idxmin(), '期号']
            
            col_res_left, col_res_right = st.columns([1.5, 3])
            with col_res_left:
                st.markdown(f"""
                <div class="custom-card" style="border-left: 4px solid {'#22C55E' if total_pl >= 0 else '#EF4444'}; padding: 15px; background: #121212; border-radius: 6px;">
                    <div class="text-gray" style="color: #A3A3A3; font-size: 13px;">累计盈亏 (元)</div>
                    <div class="huge-number" style="font-size: 36px; font-weight: 800; color: {'#22C55E' if total_pl >= 0 else '#EF4444'};">¥ {total_pl:,.2f}</div>
                    <div class="peak-text-pos" style="color: #22C55E; font-size: 13px; margin-top: 5px;">最高: ¥ {s_peak_value:,.2f} ({s_peak_issue}期)</div>
                    <div class="peak-text-neg" style="color: #EF4444; font-size: 13px; margin-top: 3px;">最低: ¥ {s_low_peak_value:,.2f} ({s_low_peak_issue}期)</div>
                </div>
                <div class="custom-card" style="margin-top: 10px; padding: 15px; background: #121212; border-radius: 6px;">
                    <div class="text-gray" style="color: #A3A3A3; font-size: 13px;">胜率</div>
                    <div class="huge-number" style="color: #FFF; font-size: 36px; font-weight: 800;">{win_rate:.1f}%</div>
                    <div class="text-gray" style="color: #A3A3A3; font-size: 13px; margin-top: 5px;">命中 {win_periods} / 总 {len(res_df)} 期</div>
                </div>
                """, unsafe_allow_html=True)
            
            with col_res_right:
                fig = px.line(res_df, x="期号", y="累计盈亏", markers=True, template="plotly_dark")
                fig.update_layout(
                    plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', 
                    height=st.session_state.chart_height + 30, margin=dict(l=10, r=10, t=25, b=5), 
                    title=dict(text="单方案盈亏走势", font=dict(size=12, color="#A3A3A3")), 
                    yaxis=dict(dtick=st.session_state.dtick_val)
                )
                fig.add_scatter(x=[s_peak_issue], y=[s_peak_value], mode='markers+text', text=[f"最高: {s_peak_value:,.0f}"], textposition="top center", marker=dict(size=8, color='#22C55E', symbol='star'), textfont=dict(color='#22C55E', size=10))
                fig.add_scatter(x=[s_low_peak_issue], y=[s_low_peak_value], mode='markers+text', text=[f"最低: {s_low_peak_value:,.0f}"], textposition="bottom center", marker=dict(size=8, color='#EF4444', symbol='star'), textfont=dict(color='#EF4444', size=10))
                fig.update_traces(selector=dict(type='scatter', mode='lines+markers'), line_color='#22C55E', line_width=2)
                st.plotly_chart(fig, use_container_width=True, key=f"chart_{scheme['id']}")