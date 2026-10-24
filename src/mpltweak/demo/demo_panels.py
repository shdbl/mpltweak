# ============================================================
# 目的: mpltweak 交互调图 demo（纯 matplotlib，零额外依赖）
#       仿照 cartopy 版的 2x2 布局，但把地图换成不需要 cartopy 的合成场面板
#       (a) 涡旋场 contourf + 等值线 (b) 流线 streamplot
#       (c) Hovmöller 时纬图 pcolormesh (d) 散点 + 拟合线 + 第三变量色标
# 数据: 全部为合成场（高斯涡旋/纬向波/正弦时间序列），不含任何真实观测
# 配置: 只出 PNG；dpi 150（demo 用，不必 300）
# 口径: 合成参数均为演示选择，不代表任何科学结论
# ============================================================
# %% 导入库
import matplotlib
matplotlib.use('Agg')            # 批处理后端；mpltweak 启动器会自动兼容

import numpy as np
import matplotlib.pyplot as plt

# %% 常量（演示口径，置顶便于调）
CMAP = 'RdBu_r'                          # 发散色标：正负异常
LEVELS = np.arange(-3.0, 3.001, 0.3)     # 共享等值线级别（21 级）
QUIVER_STEP = 6                          # 流线/箭头抽样步长
DPI = 150                                # demo 出图分辨率
FIG_W, FIG_H = 12.0, 9.0                 # 画布尺寸（英寸）
OUT_PNG = 'demo_panels.png'              # 输出文件名

# %% 生成经纬向合成场
lon = np.arange(0.5, 360.5, 1.0)         # 经度 0.5..359.5，n=360
lat = np.arange(-89.5, 90.5, 1.0)        # 纬度 -89.5..89.5，n=180
LON2D, LAT2D = np.meshgrid(lon, lat)
lon_r = np.deg2rad(LON2D)


def gauss2d(clon, clat, slon, slat):
    """二维高斯涡旋：中心 (clon, clat)，东西/南北展宽 (slon, slat)"""
    return np.exp(-((LON2D - clon) ** 2 / (2 * slon ** 2)
                    + (LAT2D - clat) ** 2 / (2 * slat ** 2)))


# (a)(b) 共用的场：一个极区涡旋 + 中纬偶极子 + 纬向 3 波
z_main = (-2.2 * gauss2d(180, 78, 55, 15)
          + 2.0 * gauss2d(320, 50, 20, 10)
          - 2.0 * gauss2d(340, 30, 15, 8)
          + 1.0 * np.sin(3 * lon_r) * np.exp(-((LAT2D - 60) / 20) ** 2))

# (c) Hovmöller：时间 × 纬度的传播型波列（相位随时间西移）
t = np.arange(0, 60, 1.0)                 # 60 个时次
T2D, LAT_C = np.meshgrid(t, lat)
hov = (np.cos(np.deg2rad(LAT_C) * 4
              - 2 * np.pi * T2D / 40.0)
       * np.exp(-((LAT_C - 30) / 35) ** 2)
       + 0.6 * np.exp(-((LAT_C + 40) / 20) ** 2))

# (d) 散点：x 为季节指数，y 为目标变量，第三变量作颜色
rng = np.random.default_rng(2026)         # 固定种子，保证每次画出一致
n_pts = 120
sc_x = rng.normal(0.0, 1.0, n_pts)
sc_c = rng.normal(0.0, 1.0, n_pts)
sc_y = 0.8 * sc_x + 0.5 * sc_c + rng.normal(0.0, 0.6, n_pts)
fit_k, fit_b = np.polyfit(sc_x, sc_y, 1)  # 一次拟合
fit_xx = np.linspace(sc_x.min(), sc_x.max(), 50)

# %% 画图：2x2 多子图（每个面板独立 add_subplot，名字清晰）
fig = plt.figure(figsize=(FIG_W, FIG_H))

# (a) 涡旋场：填色 + 等值线（共享 colorbar 的数据源）
ax0 = fig.add_subplot(221)
cf = ax0.contourf(lon, lat, z_main, levels=LEVELS, cmap=CMAP, extend='both')
cl = ax0.contour(lon, lat, z_main, levels=LEVELS[::3], colors='k',
                 linewidths=0.5, alpha=0.6)
ax0.set_xlim(0, 360)
ax0.set_ylim(-90, 90)
ax0.set_xticks([0, 90, 180, 270, 360])
ax0.set_yticks([-90, -60, -30, 0, 30, 60, 90])
ax0.set_xlabel('longitude (°)')
ax0.set_ylabel('latitude (°)')
ax0.set_title('(a) Synthetic anomaly field', fontsize=12)
ax0.grid(True, linestyle='--', linewidth=0.4, alpha=0.5)

# (b) 流线：把同一个场当成流函数，画出流动形态
ax1 = fig.add_subplot(222)
ax1.streamplot(lon, lat, np.gradient(z_main, axis=1), np.gradient(z_main, axis=0),
               density=1.2, linewidth=0.8, color='#333333', arrowstyle='->')
ax1.set_xlim(0, 360)
ax1.set_ylim(-90, 90)
ax1.set_xticks([0, 90, 180, 270, 360])
ax1.set_xlabel('longitude (°)')
ax1.set_ylabel('latitude (°)')
ax1.set_title('(b) Flow structure (streamlines)', fontsize=12)

# (c) Hovmöller：时间-纬度填色
ax2 = fig.add_subplot(223)
mesh = ax2.pcolormesh(t, lat, hov, cmap='RdBu_r', shading='auto')
ax2.set_xlabel('time (days)')
ax2.set_ylabel('latitude (°)')
ax2.set_title('(c) Time-latitude diagram', fontsize=12)
ax2.set_yticks([-90, -60, -30, 0, 30, 60, 90])

# (d) 散点 + 拟合 + 第三变量色标
ax3 = fig.add_subplot(224)
sca = ax3.scatter(sc_x, sc_y, c=sc_c, cmap='viridis', s=38,
                  edgecolors='white', linewidths=0.5, alpha=0.9)
ax3.plot(fit_xx, fit_k * fit_xx + fit_b, color='k', linewidth=1.4,
         label='fit: y = %.2fx %+.2f' % (fit_k, fit_b))
ax3.set_xlabel('seasonal index')
ax3.set_ylabel('target variable')
ax3.set_title('(d) Scatter with linear fit', fontsize=12)
ax3.legend(loc='upper left', fontsize=9)
ax3.grid(True, linestyle='--', linewidth=0.4, alpha=0.5)

# 共享 colorbar：(a) 一个 (d) 一个，各自贴在右侧
cbar0 = fig.colorbar(cf, ax=ax0, pad=0.02, shrink=0.85)
cbar0.set_label('anomaly', fontsize=10)
cbar3 = fig.colorbar(sca, ax=ax3, pad=0.02, shrink=0.85)
cbar3.set_label('third variable', fontsize=10)

fig.tight_layout(pad=1.2)

# %% 存图
fig.savefig(OUT_PNG, dpi=DPI, facecolor='white')
print('已保存:', OUT_PNG)
