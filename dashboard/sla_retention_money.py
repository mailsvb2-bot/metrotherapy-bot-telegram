"""
Аналитика: SLA → Удержание → Деньги
---------------------------------
Иллюстративные, но реалистичные графики.
Можно заменить синтетические данные на реальные из sla_metrics.
"""

import matplotlib.pyplot as plt
import numpy as np

sla_latency_ms = np.array([200, 400, 600, 800, 1000, 1200])
retention_rate = np.array([0.55, 0.50, 0.44, 0.36, 0.28, 0.18])
arpu = np.array([5200, 4800, 4200, 3500, 2600, 1800])  # ₽ / месяц
monthly_revenue = retention_rate * arpu * 10000  # база 10 000 MAU


def build_figures() -> list[plt.Figure]:
    figures: list[plt.Figure] = []

    fig = plt.figure()
    plt.plot(sla_latency_ms, retention_rate, marker='o')
    plt.xlabel("Задержка SLA (мс)")
    plt.ylabel("Месячное удержание")
    plt.title("Задержка SLA → Удержание")
    plt.grid(True)
    figures.append(fig)

    fig = plt.figure()
    plt.plot(retention_rate, arpu, marker='o')
    plt.xlabel("Месячное удержание")
    plt.ylabel("ARPU (₽)")
    plt.title("Удержание → ARPU")
    plt.grid(True)
    figures.append(fig)

    fig = plt.figure()
    plt.plot(sla_latency_ms, monthly_revenue, marker='o')
    plt.xlabel("Задержка SLA (мс)")
    plt.ylabel("Выручка в месяц (₽)")
    plt.title("Задержка SLA → Выручка (10 000 MAU)")
    plt.grid(True)
    figures.append(fig)

    return figures


if __name__ == '__main__':
    build_figures()
    plt.show()
