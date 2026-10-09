# 合成夹具脚本：用于 writeback 端到端测试（快、无外部数据依赖）
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
ax1, ax2 = axes
ax1.plot([1, 2, 3], [1, 4, 9], label='series')
ax1.legend(loc='upper left')
data = np.arange(12).reshape(3, 4)
im = ax2.imshow(data, cmap='viridis')
fig.colorbar(im, ax=ax2)
plt.tight_layout()
# ===== 自动布局调整（由调图工具生成，勿手改；重复写回会整体替换）=====
for _ax, _p in zip(fig.axes, [
    [0.0500, 0.1000, 0.4200, 0.7800],
    [0.5500, 0.1000, 0.3600, 0.7800],
    [0.9300, 0.1000, 0.0300, 0.7800],
]):
    _ax.set_position(_p)
fig.axes[0].xaxis.label.set_fontsize(12)
fig.axes[0].yaxis.label.set_fontsize(12)
fig.axes[0].tick_params(labelsize=10)
fig.axes[0].grid(True)
fig.axes[0].spines['top'].set_visible(False)
fig.axes[0].spines['right'].set_visible(False)
fig.axes[0].lines[0].set_linewidth(2.5)
fig.axes[0].lines[0].set_color('#d62728')
fig.axes[0].legend(loc='upper right', fontsize=11)
fig.axes[1].title.set_fontsize(13)
fig.axes[1].grid(False)
im.set_clim(0, 15)
im.set_cmap('plasma')
# colorbar 轴：先解除自动定位/盒比，否则重绘会重置位置与宽度
fig.axes[2].set_axes_locator(None)
fig.axes[2].set_box_aspect(None)
fig.axes[2].grid(False)
fig.axes[2]._colorbar.mappable.set_clim(0, 15)
fig.axes[2]._colorbar.mappable.set_cmap('plasma')
# ===== 自动布局调整结束 =====
fig.savefig('out.png')
print('savefig done')
