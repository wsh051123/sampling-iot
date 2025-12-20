import streamlit as st
import requests
import pandas as pd
import json

# 页面配置
st.set_page_config(page_title="OneNET 物联网控制台", layout="wide")

st.title("☁️ OneNET 物联网设备控制面板")

# --- 侧边栏：配置区域 ---
st.sidebar.header("⚙️ 连接配置")

# 尝试从 st.secrets 获取配置 (用于云端部署)，如果没有则显示输入框
# 在本地运行时，你可以创建一个 .streamlit/secrets.toml 文件来存储这些信息
default_api_key = st.secrets.get("ONENET_API_KEY", "")
default_device_id = st.secrets.get("ONENET_DEVICE_ID", "")

api_key = st.sidebar.text_input("API Key (Master-APIkey)", value=default_api_key, type="password")
device_id = st.sidebar.text_input("设备 ID (Device ID)", value=default_device_id)

# 常用 API 地址 (旧版多协议接入)
# 如果是新版 Studio，地址可能是 https://open.onenet.hk.chinamobile.com/...
base_url = "http://api.heclouds.com/devices"

if not api_key or not device_id:
    st.warning("👈 请在侧边栏输入 OneNET 的设备 ID 和 API Key 才能开始。")
    st.stop()

# --- 功能函数 ---

def get_device_data():
    """获取设备最新数据流"""
    url = f"{base_url}/{device_id}/datapoints"
    headers = {
        "api-key": api_key
    }
    try:
        response = requests.get(url, headers=headers, timeout=5)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        st.error(f"获取数据失败: {e}")
        return None

def send_command(cmd_string):
    """发送命令到设备 (CMD)"""
    # 注意：这是旧版 OneNET 的命令下发接口
    # 如果是新版，可能需要使用属性设置 (Property Set) 接口
    url = f"http://api.heclouds.com/cmds?device_id={device_id}"
    headers = {
        "api-key": api_key
    }
    try:
        # 发送字符串命令
        response = requests.post(url, headers=headers, data=cmd_string, timeout=5)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        st.error(f"发送命令失败: {e}")
        return None

# --- 主界面布局 ---

col1, col2 = st.columns(2)

with col1:
    st.subheader("📡 实时数据 (读)")
    
    if st.button("🔄 刷新数据"):
        data = get_device_data()
        if data and data.get('errno') == 0:
            streams = data.get('data', {}).get('datastreams', [])
            if streams:
                for stream in streams:
                    stream_id = stream.get('id')
                    current_value = stream.get('value')
                    update_time = stream.get('at')
                    
                    st.metric(label=stream_id, value=current_value, delta=f"更新于: {update_time}")
            else:
                st.info("暂无数据流信息")
        else:
            st.error(f"API 返回错误: {data}")

with col2:
    st.subheader("🎮 设备控制 (写)")
    
    # 示例控制：开关
    st.write("发送命令到设备:")
    
    cmd_input = st.text_input("输入自定义命令 (例如: LED_ON)", "LED_ON")
    
    if st.button("🚀 发送命令"):
        res = send_command(cmd_input)
        if res and res.get('errno') == 0:
            st.success(f"命令 '{cmd_input}' 发送成功! (cmd_uuid: {res.get('data', {}).get('cmd_uuid')})")
        else:
            st.error(f"发送失败: {res}")

    st.markdown("---")
    st.caption("提示：此控制面板默认使用 OneNET 旧版多协议接入 API。如果您使用的是新版 OneNET Studio，需要修改代码中的 API URL。")
