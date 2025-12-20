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
        
        if data.get("code") == 0:
            return True, "指令下发成功"
        else:
            return False, f"API 错误: {data.get('msg')}"
    except Exception as e:
        return False, f"请求失败: {e}"

# ==========================================
# Streamlit 页面逻辑
# ==========================================

st.set_page_config(
    page_title="OneNET 物联网控制台",
    page_icon="☁️",
    layout="wide"
)

st.title("☁️ OneNET 远程控制台 (ESP32)")
st.caption(f"Product ID: {PRODUCT_ID} | Device: {DEVICE_NAME}")

# --- 侧边栏：控制面板 ---
with st.sidebar:
    st.header("🎮 远程控制")
    
    # 1. 采集控制
    st.subheader("采集开关")
    col_sw1, col_sw2 = st.columns(2)
    with col_sw1:
        if st.button("▶️ 开始采集", type="primary"):
            success, msg = set_device_property({"enable": True})
            if success:
                st.success(msg)
            else:
                st.error(msg)
    with col_sw2:
        if st.button("⏹️ 停止采集"):
            success, msg = set_device_property({"enable": False})
            if success:
                st.success(msg)
            else:
                st.error(msg)
                
    st.divider()
    
    # 2. PGA 设置
    st.subheader("PGA 增益设置")
    pga_option = st.selectbox("选择 PGA 倍数", [1, 2, 64, 128], index=3)
    if st.button("设置 PGA"):
        success, msg = set_device_property({"pga": pga_option})
        if success:
            st.success(f"已发送 PGA={pga_option}")
            # 固件端增加了指令序列延时 (C -> 1 -> Val)，此处稍作等待
            time.sleep(0.5)
        else:
            st.error(msg)
            
    st.divider()
    
    # 3. 采样率设置
    st.subheader("采样率设置")
    # 对应 ESP32 固件逻辑: 0=10Hz, 1=40Hz, 2=640Hz, 3=1280Hz
    rate_map = {"10 Hz": 0, "40 Hz": 1, "640 Hz": 2, "1280 Hz": 3}
    rate_option = st.selectbox("选择采样率", list(rate_map.keys()), index=0)
    if st.button("设置采样率"):
        val = rate_map[rate_option]
        success, msg = set_device_property({"mode": val})
        if success:
            st.success(f"已发送 Mode={val} ({rate_option})")
            # 固件端增加了指令序列延时 (F -> Val)，此处稍作等待
            time.sleep(0.5)
        else:
            st.error(msg)

# --- 主页面：数据展示 ---

# 自动刷新逻辑
if 'auto_refresh' not in st.session_state:
    st.session_state.auto_refresh = False

col_ctrl, col_status = st.columns([1, 3])
with col_ctrl:
    if st.button("🔄 刷新数据"):
        st.rerun()
    
    # 自动刷新开关 (注意：Streamlit Cloud 上频繁刷新可能会有延迟)
    auto = st.checkbox("自动刷新 (每3秒)", value=st.session_state.auto_refresh)
    if auto:
        st.session_state.auto_refresh = True
    else:
        st.session_state.auto_refresh = False

# 获取最新数据
voltage_val, voltage_time = get_device_property("voltage")
pga_val, _ = get_device_property("pga")

# 展示数据卡片
col1, col2, col3 = st.columns(3)

with col1:
    # 安全转换电压值为浮点数
    try:
        v_display = f"{float(voltage_val):.4f} V" if voltage_val is not None else "--"
    except (ValueError, TypeError):
        v_display = f"{voltage_val} V" if voltage_val is not None else "--"

    st.metric(
        label="当前电压 (Voltage)",
        value=v_display,
        delta="实时" if voltage_val is not None else None
    )

with col2:
    st.metric(
        label="当前 PGA",
        value=f"x{pga_val}" if pga_val is not None else "--"
    )

with col3:
    # 简单计算最后更新时间距离现在多久
    if voltage_time:
        try:
            # OneNET 返回的时间戳通常是毫秒
            last_time = int(voltage_time) / 1000.0
            diff = time.time() - last_time
            time_str = f"{diff:.1f} 秒前"
        except:
            time_str = str(voltage_time)
    else:
        time_str = "--"
        
    st.metric(
        label="最后更新时间",
        value=time_str
    )

# 历史数据图表 (模拟)
# 注意：OneNET 获取历史数据 API 比较复杂，这里暂时只展示实时点
# 如果需要历史曲线，需要调用 /thingmodel/query-device-property-history
st.subheader("📈 实时数据快照")
if voltage_val is not None:
    # 维护一个简单的 session_state 列表来画图
    if 'history_data' not in st.session_state:
        st.session_state.history_data = []
    
    # 添加新数据 (去重，防止刷新导致重复点)
    # 尝试将 voltage_val 转为 float，如果失败则不添加
    try:
        v_float = float(voltage_val)
        current_entry = {"time": time.strftime("%H:%M:%S"), "voltage": v_float}
        
        if not st.session_state.history_data or st.session_state.history_data[-1]["time"] != current_entry["time"]:
            st.session_state.history_data.append(current_entry)
    except:
        pass
        
    # 保持最近 30 个点
    if len(st.session_state.history_data) > 30:
        st.session_state.history_data.pop(0)
        
    if st.session_state.history_data:
        df = pd.DataFrame(st.session_state.history_data)
        
        # --- 1. 统计数据 ---
        m1, m2, m3 = st.columns(3)
        m1.metric("最高电压", f"{df['voltage'].max():.4f} V")
        m2.metric("最低电压", f"{df['voltage'].min():.4f} V")
        m3.metric("平均电压", f"{df['voltage'].mean():.4f} V")
        
        # --- 2. 美化图表 (Altair) ---
        # 动态计算 Y 轴范围，让波动看起来更明显
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
            height=350
        ).interactive()
        
        st.altair_chart(chart, use_container_width=True)
    else:
        st.info("等待数据积累...")
else:
    st.info("暂无数据，请确保设备在线并已开始采集。")

# 自动刷新触发
if st.session_state.auto_refresh:
    time.sleep(3)
    st.rerun()
