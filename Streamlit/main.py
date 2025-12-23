import streamlit as st
import requests
import time
import pandas as pd
import json
import base64
import hmac
import hashlib
import altair as alt
from urllib.parse import quote

# ==========================================
# 配置区域
# ==========================================

# OneNET 基础信息
PRODUCT_ID = "6R9kiumZF1"
DEVICE_NAME = "ESP32"
ACCESS_KEY = "GdFdkQGP1YsRv129daPTa+nV07XtGSmjQ0ERl91jIRk="  # 用户提供的 AccessKey

# OneNET Studio API 地址
BASE_URL = "https://iot-api.heclouds.com"

# ==========================================
# 核心逻辑函数
# ==========================================

# 使用 ESP32 代码中已验证可用的 Token
# 注意：这个 Token 有效期到 2030 年 (et=1923202207)
FIXED_TOKEN = "version=2018-10-31&res=products%2F6R9kiumZF1%2Fdevices%2FESP32&et=1923202207&method=md5&sign=S9SRMkTDgNQcH9lEVh%2Bnew%3D%3D"

def get_token(res):
    """
    直接返回已知的可用 Token，跳过本地计算，避免 Key 或算法不匹配的问题
    """
    return FIXED_TOKEN

# def get_token_dynamic(res):
#     """
#     (已禁用) 动态生成 Token
#     """
#     version = "2018-10-31"
    # 过期时间：当前时间 + 100天 (简单起见)
    et = int(time.time()) + 3600 * 24 * 100
    method = "md5" # 改为 md5 以匹配 ESP32 的配置
    
    # 构造签名字符串
    # res 需要 URL Encode
    res_encoded = quote(res, safe='')
    sign_str = f"{et}\n{method}\n{res_encoded}\n{version}"
    
    # 计算 HMAC-MD5
    key = base64.b64decode(ACCESS_KEY)
    sign = base64.b64encode(hmac.new(key, sign_str.encode('utf-8'), hashlib.md5).digest()).decode('utf-8')
    sign_encoded = quote(sign, safe='')
    
    # 拼接最终 Token
    token = f"version={version}&res={res_encoded}&et={et}&method={method}&sign={sign_encoded}"
    return token

def get_device_property(property_name):
    """
    查询设备属性最新值
    API: /thingmodel/query-device-property
    """
    url = f"{BASE_URL}/thingmodel/query-device-property"
    
    # 资源标识符
    res = f"products/{PRODUCT_ID}/devices/{DEVICE_NAME}"
    token = get_token(res)
    
    headers = {
        "Authorization": token
    }
    
    params = {
        "product_id": PRODUCT_ID,
        "device_name": DEVICE_NAME
    }
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=5)
        response.raise_for_status()
        data = response.json()
        
        if data.get("code") == 0:
            # 解析属性列表
            properties = data.get("data", [])
            for prop in properties:
                if prop.get("identifier") == property_name:
                    return prop.get("value"), prop.get("time")
            return None, None
        else:
            st.error(f"API 错误: {data.get('msg')}")
            return None, None
    except Exception as e:
        st.error(f"请求失败: {e}")
        return None, None

def set_device_property(params_dict):
    """
    下发设备属性设置指令
    API: /thingmodel/set-device-property
    """
    url = f"{BASE_URL}/thingmodel/set-device-property"
    
    res = f"products/{PRODUCT_ID}/devices/{DEVICE_NAME}"
    token = get_token(res)
    
    headers = {
        "Authorization": token,
        "Content-Type": "application/json"
    }
    
    body = {
        "product_id": PRODUCT_ID,
        "device_name": DEVICE_NAME,
        "params": params_dict
    }
    
    try:
        response = requests.post(url, headers=headers, json=body, timeout=5)
        response.raise_for_status()
        data = response.json()
        
        # 记录日志
        if 'cmd_logs' not in st.session_state:
            st.session_state.cmd_logs = []
        
        timestamp = time.strftime("%H:%M:%S")
        
        if data.get("code") == 0:
            msg = "指令下发成功"
            st.session_state.cmd_logs.insert(0, f"[{timestamp}] ✅ 成功: {params_dict}")
            return True, msg
        else:
            msg = f"API 错误: {data.get('msg')}"
            st.session_state.cmd_logs.insert(0, f"[{timestamp}] ❌ 失败: {params_dict} - {msg}")
            return False, msg
    except Exception as e:
        if 'cmd_logs' not in st.session_state:
            st.session_state.cmd_logs = []
        timestamp = time.strftime("%H:%M:%S")
        st.session_state.cmd_logs.insert(0, f"[{timestamp}] ❌ 异常: {params_dict} - {e}")
        return False, f"请求失败: {e}"

# ==========================================
# Streamlit 页面逻辑
# ==========================================

st.set_page_config(
    page_title="物联网控制台",
    page_icon="☁️",
    layout="wide",
    initial_sidebar_state="expanded"
)
# --- 登录认证 ---
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False

if not st.session_state.logged_in:
    st.markdown("""
        <style>
        .block-container {padding-top: 5rem;}
        </style>
    """, unsafe_allow_html=True)
    
    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        st.title("🔒 系统登录")
        st.caption("默认账号: admin / 123456")
        
        with st.form("login_form"):
            username = st.text_input("用户名")
            password = st.text_input("密码", type="password")
            submit = st.form_submit_button("登录", type="primary", use_container_width=True)
            
            if submit:
                if username == "admin" and password == "123456":
                    st.session_state.logged_in = True
                    st.rerun()
                else:
                    st.error("用户名或密码错误")
    
    st.stop()
# 自定义 CSS 样式
st.markdown("""
    <style>
    .stMetric {
        background-color: #f0f2f6;
        padding: 10px;
        border-radius: 5px;
    }
    </style>
    """, unsafe_allow_html=True)

st.title("☁️ 控制台 ")
st.caption(f"Product ID: {PRODUCT_ID} | Device: {DEVICE_NAME}")

# 初始化 Session State
if 'auto_refresh' not in st.session_state:
    st.session_state.auto_refresh = False
if 'history_data' not in st.session_state:
    st.session_state.history_data = []
if 'cmd_logs' not in st.session_state:
    st.session_state.cmd_logs = []

# --- 侧边栏：控制面板 ---
with st.sidebar:
    # 用户信息与注销
    with st.container():
        col_user, col_logout = st.columns([2, 1])
        with col_user:
            st.write("👤 **管理员**")
        with col_logout:
            if st.button("退出", key="logout_btn", use_container_width=True):
                st.session_state.logged_in = False
                st.rerun()
    st.divider()

    st.header("🎮 远程控制")
    
    # 1. 采集控制
    with st.expander("📡 采集控制", expanded=True):
        col_sw1, col_sw2 = st.columns(2)
        with col_sw1:
            if st.button("▶️ 开始", type="primary", use_container_width=True):
                success, msg = set_device_property({"enable": True})
                if success: st.toast(msg, icon="✅")
                else: st.toast(msg, icon="❌")
        with col_sw2:
            if st.button("⏹️ 停止", use_container_width=True):
                success, msg = set_device_property({"enable": False})
                if success: st.toast(msg, icon="✅")
                else: st.toast(msg, icon="❌")
    
    # 2. 参数设置
    with st.expander("⚙️ 参数设置", expanded=True):
        # PGA 设置
        pga_option = st.selectbox("PGA 增益", [1, 2, 64, 128], index=3)
        if st.button("应用 PGA 设置", use_container_width=True):
            success, msg = set_device_property({"pga": pga_option})
            if success: 
                st.toast(f"已发送 PGA={pga_option}", icon="✅")
                time.sleep(0.5)
            else: st.toast(msg, icon="❌")
            
        st.divider()
        
        # 采样率设置
        rate_map = {"10 Hz": 0, "40 Hz": 1, "640 Hz": 2, "1280 Hz": 3}
        rate_option = st.selectbox("采样率", list(rate_map.keys()), index=0)
        if st.button("应用采样率设置", use_container_width=True):
            val = rate_map[rate_option]
            success, msg = set_device_property({"mode": val})
            if success: 
                st.toast(f"已发送 Mode={val}", icon="✅")
                time.sleep(0.5)
            else: st.toast(msg, icon="❌")

    st.divider()
    
    # 3. 系统设置
    st.subheader("🛠️ 系统设置")
    # 自动刷新
    auto = st.toggle("自动刷新 (3s)", value=st.session_state.auto_refresh)
    if auto:
        st.session_state.auto_refresh = True
    else:
        st.session_state.auto_refresh = False
        
    if st.button("🗑️ 清空历史数据", use_container_width=True):
        st.session_state.history_data = []
        st.rerun()
        
    if st.button("🧹 清空操作日志", use_container_width=True):
        st.session_state.cmd_logs = []
        st.rerun()

# --- 主页面逻辑 ---

# 获取最新数据
voltage_val, voltage_time = get_device_property("voltage")
pga_val, _ = get_device_property("pga")

# 数据处理与缓存
if voltage_val is not None:
    try:
        v_float = float(voltage_val)
        current_entry = {"time": time.strftime("%H:%M:%S"), "voltage": v_float}
        
        # 简单去重：如果时间和数值都一样，或者时间非常接近（这里只判断时间字符串）
        if not st.session_state.history_data or st.session_state.history_data[-1]["time"] != current_entry["time"]:
            st.session_state.history_data.append(current_entry)
    except:
        pass
    
    # 保持最近 50 个点
    if len(st.session_state.history_data) > 50:
        st.session_state.history_data.pop(0)

# 顶部指标栏
m1, m2, m3, m4 = st.columns(4)

with m1:
    try:
        v_display = f"{float(voltage_val):.4f} V" if voltage_val is not None else "--"
    except:
        v_display = f"{voltage_val} V" if voltage_val is not None else "--"
    st.metric("⚡ 当前电压", v_display)

with m2:
    st.metric("🎚️ 当前 PGA", f"x{pga_val}" if pga_val is not None else "--")

with m3:
    # 计算最后更新时间
    if voltage_time:
        try:
            last_time = int(voltage_time) / 1000.0
            diff = time.time() - last_time
            if diff < 60:
                time_str = f"{diff:.0f} 秒前"
            else:
                time_str = f"{diff/60:.0f} 分钟前"
        except:
            time_str = "--"
    else:
        time_str = "--"
    st.metric("🕒 最后更新", time_str)

with m4:
    # 简单判断在线状态：如果最后更新时间在 5 分钟内，认为在线
    is_online = False
    if voltage_time:
        try:
            last_time = int(voltage_time) / 1000.0
            if time.time() - last_time < 300:
                is_online = True
        except:
            pass
            
    status = "🟢 在线" if is_online else "🔴 离线/未知"
    st.metric("📡 设备状态", status)

# 页面主体 Tabs
tab1, tab2, tab3 = st.tabs(["📈 实时监控", "📊 数据明细", "📝 操作日志"])

with tab1:
    if st.session_state.history_data:
        df = pd.DataFrame(st.session_state.history_data)
        
        # 统计信息
        c1, c2, c3 = st.columns(3)
        c1.info(f"最高: {df['voltage'].max():.4f} V")
        c2.info(f"最低: {df['voltage'].min():.4f} V")
        c3.info(f"平均: {df['voltage'].mean():.4f} V")
        
        # 图表
        y_min = df['voltage'].min() * 0.95
        y_max = df['voltage'].max() * 1.05
        if y_min == y_max:
            y_min -= 0.1
            y_max += 0.1

        chart = alt.Chart(df).mark_area(
            line={'color':'#FF4B4B'},
            color=alt.Gradient(
                gradient='linear',
                stops=[alt.GradientStop(color='#FF4B4B', offset=0),
                       alt.GradientStop(color='white', offset=1)],
                x1=1, x2=1, y1=1, y2=0
            )
        ).encode(
            x=alt.X('time', title='时间'),
            y=alt.Y('voltage', title='电压 (V)', scale=alt.Scale(domain=[y_min, y_max])),
            tooltip=['time', 'voltage']
        ).properties(
            height=400
        ).interactive()
        
        st.altair_chart(chart, use_container_width=True)
    else:
        st.info("暂无历史数据，请等待数据刷新...")

with tab2:
    if st.session_state.history_data:
        df = pd.DataFrame(st.session_state.history_data)
        st.dataframe(df, use_container_width=True)
        
        csv = df.to_csv(index=False).encode('utf-8')
        st.download_button(
            "📥 下载 CSV 数据",
            csv,
            "voltage_data.csv",
            "text/csv",
            key='download-csv'
        )
    else:
        st.info("暂无数据")

with tab3:
    if st.session_state.cmd_logs:
        for log in st.session_state.cmd_logs:
            st.text(log)
    else:
        st.caption("暂无操作日志")

# 自动刷新触发
if st.session_state.auto_refresh:
    time.sleep(3)
    st.rerun()
