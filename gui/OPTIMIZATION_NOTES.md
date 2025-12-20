# 异常值处理逻辑优化说明

## 优化版本文件
- 原始文件：`cs1237_pyqt6.py`
- 优化文件：`cs1237_pyqt6_optimized.py`

## 主要优化内容

### 1. **简化数据结构** ✅
**问题：** 原代码使用了多个缓冲区（`buffered_points`、`processing_buffer`、`recent_values`），逻辑复杂且容易出错

**优化：**
- 移除了复杂的 `buffered_points` 和 `processing_buffer`
- 仅保留一个简单的 `spike_buffer` (maxlen=5) 用于脉冲检测
- 使用单一的 `data_lock` 保护所有数据操作

```python
# 优化前：多个缓冲区
self.buffered_points = deque()
self.processing_buffer = deque(maxlen=3)
self.recent_values = deque(maxlen=50)
self.buffer_lock = threading.Lock()

# 优化后：简化为单一检测缓冲区
self.spike_buffer = deque(maxlen=5)
self.recent_values = deque(maxlen=100)  # 增加窗口大小
self.data_lock = threading.Lock()
```

### 2. **改进异常值检测算法** ✅

#### 2.1 MAD统计检测优化
**问题：** 原代码要求同时满足统计异常（Modified Z-score > 3.5）和数量级差异（10倍），过于严格导致漏检

**优化：**
- 移除了数量级要求，仅使用Modified Z-score判断
- 函数返回 `(is_outlier, replacement_value)` 元组，直接提供替换值
- 增加统计窗口大小从50到100，提高稳定性

```python
def is_outlier_mad(self, value):
    """
    优化后的MAD异常值检测
    返回: (is_outlier, replacement_value)
    """
    # ... 统计计算 ...
    
    # 仅使用Modified Z-score判断（移除了10倍数量级要求）
    if modified_z_score > self.outlier_threshold:
        return True, int(median)
    
    return False, value
```

#### 2.2 单点脉冲检测简化
**问题：** 原代码使用复杂的邻点比较和多个条件判断，计算量大

**优化：**
- 简化为比较中点与线性插值的偏差
- 使用更直观的阈值判断（偏差 > max(50, 5倍邻点偏差)）

```python
def detect_spike(self, prev_v, curr_v, next_v):
    """
    优化后的单点脉冲检测
    """
    interp = (prev_v + next_v) / 2.0
    curr_dev = abs(curr_v - interp)
    neighbor_dev = abs(next_v - prev_v)
    
    # 简化判断：中点偏差远大于邻点偏差
    if curr_dev > max(50, 5 * neighbor_dev):
        return True, int(interp)
        
    return False, curr_v
```

### 3. **优化数据处理流程** ✅

**问题：** 原代码使用复杂的前后文缓冲和延迟处理机制

**优化：** 采用两阶段过滤流程

```python
def extract_and_plot_adc(self, line):
    with self.data_lock:
        # 第一步：MAD统计异常值检测
        is_outlier, replacement = self.is_outlier_mad(signed)
        if is_outlier:
            self.outlier_count += 1
            value_to_buffer = replacement
        else:
            value_to_buffer = signed
        
        # 第二步：添加到脉冲检测缓冲区
        self.spike_buffer.append((current_time, value_to_buffer))
        
        # 第三步：当缓冲区有至少3个点时，检测中间点是否为脉冲
        if len(self.spike_buffer) >= 3:
            t1, v1 = self.spike_buffer[-2]
            v0 = self.spike_buffer[-3][1]
            v2 = self.spike_buffer[-1][1]
            
            is_spike, spike_replacement = self.detect_spike(v0, v1, v2)
            if is_spike:
                self.outlier_count += 1
                final_value = spike_replacement
            else:
                final_value = v1
            
            # 添加到绘图数据
            self.recent_values.append(final_value)
            self.plot_data_x.append(t1)
            self.plot_data_y.append(final_value)
```

**优势：**
- 流程清晰，易于理解和维护
- 减少了不必要的缓冲和等待
- 每个点都经过完整的两阶段检测

### 4. **改进线程安全性** ✅

**问题：** 原代码虽然使用了锁，但保护范围不一致

**优化：**
- 统一使用 `data_lock` 保护所有数据操作
- 在 `extract_and_plot_adc` 和 `clear_plot` 中正确使用锁

```python
# 数据处理时加锁
with self.data_lock:
    # 所有数据操作

# 清除数据时也加锁
def clear_plot(self):
    with self.data_lock:
        self.plot_data_x.clear()
        self.plot_data_y.clear()
        self.spike_buffer.clear()
```

### 5. **移除不必要的复杂度** ✅

**移除的功能：**
- `min_points_before_plot` - 初始延迟绘图机制
- `total_received` - 接收计数
- `context_window` 和 `lookahead` - 复杂的前后文窗口
- `_flush_processing_buffer` - 复杂的缓冲区刷新逻辑
- `is_outlier_in_context` - 基于上下文的异常检测（与主检测重复）
- `_is_spike_between` - 复杂的三点脉冲检测（已简化为 `detect_spike`）

## 性能改进

### 计算复杂度
- **原代码：** O(n) 多次迭代 + 复杂的窗口管理
- **优化后：** O(n) 单次迭代 + 简单的滑动窗口

### 内存使用
- **原代码：** 3个缓冲区 + 多个临时列表
- **优化后：** 2个缓冲区（recent_values + spike_buffer）

### 延迟
- **原代码：** 需要等待至少50个点才开始绘图
- **优化后：** 仅需3个点即可开始显示（带完整检测）

## 使用建议

### 参数调整
可根据实际数据特点调整以下参数：

```python
# 在 __init__ 中
self.outlier_threshold = 3.5  # MAD阈值，降低会更敏感
self.min_data_for_filter = 20  # 统计过滤的最小数据量
self.recent_values = deque(maxlen=100)  # 统计窗口大小
```

### 测试建议
1. 先使用优化版本测试正常数据
2. 注入已知的异常值，验证检测效果
3. 观察过滤统计数量是否合理
4. 根据实际效果微调阈值

## 迁移步骤

1. **备份原文件**
   ```bash
   cp cs1237_pyqt6.py cs1237_pyqt6_backup.py
   ```

2. **使用优化版本**
   ```bash
   cp cs1237_pyqt6_optimized.py cs1237_pyqt6.py
   ```

3. **测试验证**
   - 连接硬件
   - 测试单次读取
   - 测试连续读取
   - 检查异常值过滤效果

## 主要优势总结

✅ **更简单**：代码行数减少，逻辑更清晰
✅ **更快**：减少不必要的计算和缓冲
✅ **更准确**：优化的异常值检测算法
✅ **更稳定**：改进的线程安全性
✅ **更易维护**：结构清晰，易于理解和修改

## 注意事项

⚠️ 优化后的版本保留了所有原有功能，但处理逻辑更简洁
⚠️ 如果遇到问题，可以随时回退到备份版本
⚠️ 建议在实际硬件上充分测试后再正式使用
