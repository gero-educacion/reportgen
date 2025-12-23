import matplotlib.pyplot as plt
from io import BytesIO
from matplotlib.colors import to_rgb
from matplotlib.patches import Patch
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CHARTS_DIR = BASE_DIR / "outputs" / "charts"

CHARTS_DIR.mkdir(exist_ok=True)

def _validate_holland_data(student):
    required = [
        "% Holland 01", "% Holland 02", "% Holland 03",
        "% Holland 04", "% Holland 05", "% Holland 06"
    ]

    values = []
    for col in required:
        if col not in student:
            raise KeyError(f"Missing column: {col}")

        try:
            val = float(student[col])
        except (TypeError, ValueError):
            raise ValueError(f"Invalid value for {col}: {student[col]}")

        if val < 0:
            raise ValueError(f"Negative value for {col}: {val}")

        values.append(val)

    total = sum(values)
    if total <= 0:
        raise ValueError("Sum of Holland values is zero")

    return [v * 100 for v in values]


def generate_graph(student, save_debug):
    sid = student["Nombre y Apellido"]
    try: 
        porcentajes = _validate_holland_data(student)

        colores = [
            "#60AD9D", "#FFCA43", "#F97930",
            "#FFC665", "#8DBEB2", "#FEB58C"
        ]

        labels = [
            "SOCIAL", "ARTÍSTICO", "CONVENCIONAL",
            "REALISTA", "EMPRENDEDOR", "INVESTIGATIVO"
        ]

        fig, ax = plt.subplots(figsize=(6, 6), facecolor="white")
        ax.set_aspect("equal")
        ax.axis("off")

        # shadow
        ax.pie(
            porcentajes,
            colors=["black"] * 6,
            startangle=90,
            counterclock=False,
            radius=1.0,
            center=(0.03, -0.06),
            wedgeprops=dict(linewidth=0)
        )

        # main
        ax.pie(
            porcentajes,
            colors=colores,
            startangle=90,
            counterclock=False,
            radius=1.0,
            autopct=lambda p: f"{p:.1f}%" if p > 0 else "",
            pctdistance=0.65,
            wedgeprops=dict(edgecolor="black", linewidth=1, joinstyle="round"),
            textprops=dict(color="black", fontsize=10, fontweight="bold")
        )

        handles = [Patch(facecolor=c, edgecolor="black", linewidth=0.8) for c in colores]

        ax.legend(
            handles,
            labels,
            loc="upper left",
            bbox_to_anchor=(1.02, 1.02),
            frameon=False,
            fontsize=10
        )

        img = BytesIO()
        fig.savefig(img, format="png", dpi=200, bbox_inches="tight", pad_inches=0.15)
        img.seek(0)

        if img.getbuffer().nbytes < 10_000:
            raise RuntimeError("Chart image too small")
        if save_debug and sid:
            path = CHARTS_DIR / f"{sid}.png"
            with open(path, "wb") as f:
                f.write(img.getbuffer())
                f.close()

        plt.close(fig)
        return img
    except Exception as e:
         raise RuntimeError(
             f"Chart generation failed for student {sid}"
         ) from e
