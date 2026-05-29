# Semi-Auto Curation Studio

面向自动化探针台高通量数据的分析平台。当前已实现 `IV` 数据类型，支持：

- `CLI + GUI` 双入口
- 可扩展的 analyzer registry，便于后续增加更多数据类型
- 基于指定电压窗口的 `I-V` 线性拟合电阻分析
- 基于 `Device Array` 行列坐标绘制热图
- 热图单点选择、`Ctrl` 多选、框选
- 在第二个绘图面板联动显示一条或多条原始 `IV` 曲线
- dummy 点判定、灰色显示和对角叉标记

## 环境

项目使用 `uv` 管理：

```powershell
uv venv .venv
.venv\Scripts\Activate.ps1
uv sync
```

## 运行 GUI

```powershell
uv run semi-auto-curation
```

也可以显式写成：

```powershell
uv run semi-auto-curation gui
```

## 运行 CLI

```powershell
uv run semi-auto-curation analyze iv --source rawdata/iv --output output --fit-min 0.0 --fit-max 1.2
```

可选参数：

- `--dummy-r-min`
- `--dummy-r-max`
- `--dummy-r2`

CLI 会导出：

- `output/iv_fit_summary.csv`
- `output/iv_fit_detail.json`

## 当前 IV GUI

- 左侧：数据源、拟合窗口、dummy 判定和热图设置
- 右上：summary + heatmap
- 右下：选中器件的原始 IV 曲线
- 左下：选中器件列表和当前焦点器件详情

## 交互说明

- 单击热图像素：选择单个器件
- `Ctrl + 单击`：多选/取消选择
- 勾选 `Box Select Mode` 后拖拽：框选多个像素
- 热图右侧色条来自 `pyqtgraph` 的 `HistogramLUT`
- `Scale Min / Scale Max` 可手动限制热图颜色范围
- dummy 点显示为灰色并带对角叉

## 当前项目结构

```text
main.py
pyproject.toml
src/semi_auto_curation/
  app.py
  models.py
  analyzers/
    registry.py
  analysis/
    iv.py
  data/
    loader.py
  services/
    exporter.py
    pipeline.py
  ui/
    iv_panel.py
    main_window.py
rawdata/iv/
output/
```

## 后续扩展

后续新增数据类型时，建议沿用现在这套分层：

- `analysis/<type>.py`：数据处理和指标计算
- `ui/<type>_panel.py`：该类型专属 GUI
- `analyzers/registry.py`：注册到主工作台

这样可以在不改动主窗口骨架的情况下继续增加 `CV`、`Pulse`、`Wafer Map`、`Yield` 等分析模块。
