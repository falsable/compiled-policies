"""Gráfico de la prueba de muerte: coste total por tarea vs κ, y mapa de fase κ × ratio."""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import BoundaryNorm, ListedColormap

INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, AXIS, SURF = "#e1e0d9", "#c3c2b7", "#fcfcfb"
SERIES = {  # orden fijo de la paleta categórica (validada)
    "mdp": ("#2a78d6", "o", "MDP (workflow completo)"),
    "cascada": ("#eb6834", "s", "Cascada por paso (FrugalGPT)"),
    "router": ("#1baf7a", "D", "Router por paso (UCCI)"),
    "mdp_sin_acopl": ("#eda100", "^", "MDP que ignora la deuda"),
    "agente_libre": ("#e87ba4", "v", "Agente libre (siempre caro)"),
}
BLUES = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]


def main(dir_res: Path) -> Path:
    bk = pd.read_csv(dir_res / "barrido_kappa.csv")
    fs = pd.read_csv(dir_res / "fase.csv")
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6.2), gridspec_kw={"width_ratios": [1.15, 1]})
    fig.patch.set_facecolor(SURF)

    # ---- panel 1: coste total por tarea vs κ (ratio 25)
    ax1.set_facecolor(SURF)
    finales = []
    for pol, (col, mk, etiqueta) in SERIES.items():
        d = bk[bk.politica == pol].sort_values("kappa")
        ax1.plot(d.kappa, d.coste_total_por_tarea, color=col, lw=2, marker=mk, ms=6,
                 markeredgecolor=SURF, markeredgewidth=1.2, label=etiqueta, zorder=3)
        finales.append((d.coste_total_por_tarea.iloc[-1], etiqueta.split(" (")[0], col))
    # etiquetas directas al final de cada línea, separadas para que no choquen
    finales.sort()
    ys, minimo = [], 0.075
    for y, _, _ in finales:
        ys.append(max(y, ys[-1] + minimo) if ys else y)
    for (y, txt, col), yl in zip(finales, ys):
        ax1.annotate(txt, xy=(1.0, y), xytext=(1.03, yl), textcoords="data", va="center",
                     fontsize=9, color=INK2, annotation_clip=False,
                     arrowprops=dict(arrowstyle="-", color=col, lw=1))
    ax1.set_xlim(-0.02, 1.0)
    ax1.set_ylim(0, None)
    ax1.set_xlabel("κ — acoplamiento: cuánto daña un paso barato a los siguientes", color=INK2)
    ax1.set_ylabel("Coste total por tarea (€) — menor es mejor", color=INK2)
    ax1.set_title("Coste total por tarea según el acoplamiento\n(ratio caro/barato = 25×, V = 2 €, 4 etapas)",
                  loc="left", color=INK, fontsize=11)
    ax1.grid(axis="y", color=GRID, lw=0.8)
    ax1.legend(loc="upper left", frameon=False, fontsize=9, labelcolor=INK2)

    # ---- panel 2: mapa de fase — ahorro del MDP sobre el MEJOR baseline por paso
    ax2.set_facecolor(SURF)
    tab = fs.pivot(index="kappa", columns="ratio", values="ahorro_pct_coste_total").sort_index(ascending=False)
    bordes = [-100, 0.5, 5, 10, 15, 20, 30, 40, 100]
    cmap = ListedColormap(["#f0efec"] + BLUES)
    norm = BoundaryNorm(bordes, cmap.N)
    ax2.imshow(tab.values, cmap=cmap, norm=norm, aspect="auto")
    for i in range(tab.shape[0]):
        for j in range(tab.shape[1]):
            v = tab.values[i, j]
            oscuro = v >= 20
            ax2.text(j, i, f"{v:.0f}" if abs(v) >= 0.5 else "0", ha="center", va="center", fontsize=8.5,
                     color="#ffffff" if oscuro else INK)
    ax2.set_xticks(range(tab.shape[1]), [f"{int(c)}×" for c in tab.columns])
    ax2.set_yticks(range(tab.shape[0]), [f"{k:.1f}" for k in tab.index])
    ax2.set_xlabel("Ratio de precio entre tier caro y barato", color=INK2)
    ax2.set_ylabel("κ (acoplamiento)", color=INK2)
    ax2.set_title("% de ahorro del MDP frente al mejor baseline por paso\n(gris = no gana; números = % del coste total)",
                  loc="left", color=INK, fontsize=11)
    for ax in (ax1, ax2):
        for s in ax.spines.values():
            s.set_color(AXIS)
        ax.tick_params(colors=MUTED)
    fig.tight_layout()
    out = dir_res / "kill_test.png"
    fig.savefig(out, dpi=160, facecolor=SURF, bbox_inches="tight")
    return out


if __name__ == "__main__":
    print(main(Path(sys.argv[1] if len(sys.argv) > 1 else "resultados")))
