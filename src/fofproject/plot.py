import datetime as dt
from collections import defaultdict
from itertools import cycle
from typing import Dict

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dateutil.relativedelta import relativedelta

from fofproject.fund import Fund
from fofproject.utils import hex_to_rgba

DEFAULT_COLOR = "#D8C3A5"

fund_name_map = {
    "LEAD": {"en": "Fund", "cn": "基金"},
    "MSCI CHINA": {"en": "MSCI China Index", "cn": "MSCI 中国指数"},
    "MSCI WORLD": {"en": "MSCI World Index", "cn": "MSCI 世界指数"},
    "S&P 500": {"en": "S&P 500 Index", "cn": "标普500指数"},
    "TOPIX": {"en": "TOPIX Index", "cn": "東証株価指数"},
    "EUREKAHEDGE": {
        "en": "Eurekahedge Hedge Fund Index",
        "cn": "Eurekahedge 对冲基金指数",
        # add more mappings here...
    },
}

STYLE_DICT = {
    "default": {
        "layout_config": {
            "font": {
                "family": "Roboto, MontSerrat Semibold, sans-serif",
                "size": 14,
                "color": "#2f2f2f",
            },
            "margin": {
                "l": 70,  # left margin
                "r": 20,  # right margin
                "t": 70,  # top margin
                "b": 110,  # bottom margin
            },
            "grid_color": "#DDDDDE",
            "zero_color" :"#989A9C",
            "date_ticks": {
                "num": 6, # Number of date ticks to display
                "format": "%b" # Format for date ticks
            },
            "annotation": {
                "add_annotation": True,  # True if we want to add an annotation
                "position": {

                    "x": 0.5, # scale to the entire plot area
                    "y": -0.18 # scale to the entire plot area
                }
            },
            "legend": {
                "orientation": "h", # "h" for horizontal, "v" for vertical
                "position": {
                    "x": 0.5, # scale to the entire plot area
                    "y": -0.07 # scale to the entire plot area
                }
            },
        },
        "trace_config": {
            "mark_size": {

                "lead": 18, # marker size for the lead fund
                "other": 16 # marker size for the other funds
            },
            "line_width": {
                "lead": 5, # line width for the lead fund
                "other": 4 # line width for the other funds
            },
            "color": {
                "lead": "#2F2F2F",
                "other": [
                    "#53565A",  # light stone grey
                    "#DACEBF",  # light teal green
                    "#C1AE94",  # pale warm taup
                    "#989A9C",  # off-white with warmth
                    "#81B29A",  # pale oat
                ],
            },
            "Y-axis": True,
        },
        "markers": {"enabled": True},  # 👈 markers every ~10% of the data points
        "value_boxes": {
            "enabled": False,
            "format": "+.2%",
            "box": {
                "bgcolor": "rgba(255,255,255,0.9)",
                "borderwidth": 1,
                "font_size": 12,
                "xshift": 6,
                "yshift": 6,
            },
            # Show only for lead fund every step and final, for others only final
            "rules": {
                "lead": {"every_step": True, "final": True},
                "others": {"every_step": False, "final": True},
            },
        },
    },
    "modern_dark": {
        "layout_config": {
            "font": {
                "family": "Inter, Montserrat, system-ui, sans-serif",
                "size": 13,
                "color": "#1f2937",
            },
            "margin": {"l": 56, "r": 16, "t": 56, "b": 80},
            "background": {"paper": "#ffffff", "plot": "#fcfcfd"},
            "grid_color": "#efefef",

            "zero_color" :"#53565A",
            "date_ticks": {
                "num": 8,
                "format": "%b"
            },

            "annotation": {
                "add_annotation": False,
                "position": {"x": 0.5, "y": 1.12},
                "style": {"font_size": 14, "font_color": "#374151"},
            },
            "legend": {
                "orientation": "h",
                "position": {"x": 1.0, "y": 1.15},
                "anchor": "right",
            },
        },
        "trace_config": {
            "mark_size": {"lead": 10, "other": 6},
            "line_width": {"lead": 2.5, "other": 1.8},
            "color": {
                "lead": "#0E7CFF",
                "other": ["#8B5CF6", "#10B981", "#F59E0B", "#EF4444", "#14B8A6"],
            },
            "Y-axis": True,
        },
        "markers": {"enabled": True},
        "value_boxes": {
            "enabled": True,
            "format": "+.1%",
            "box": {
                "bgcolor": "rgba(15,23,42,0.75)",
                "borderwidth": 0,
                "font_size": 11,
                "font_color": "#F8FAFC",
                "xshift": 5,
                "yshift": 5,
                "pad": 4,
            },
            "rules": {
                "lead": {"every_step": False, "final": True},
                "others": {"every_step": False, "final": True},
            },
        },
    },
    "excel": {
        "layout_config": {
            "font": {
                "family": "Microsoft Yahei Light, Roboto, sans-serif",
                "size": 14,
                "color": "#2f2f2f",
            },
            "margin": {
                "l": 70,  # left margin
                "r": 20,  # right margin
                "t": 70,  # top margin
                "b": 110,  # bottom margin
            },
            "grid_color": "#DACEBF",
            "zero_color" :"#53565A",
            "date_ticks": {
                "num": 6,  # Number of date ticks to display
                "format": "%b %Y",  # Format for date ticks
            },
            "annotation": {
                "add_annotation": False,  # True if we want to add an annotation
                "position": {
                    "x": 0.5,  # scale to the entire plot area
                    "y": -0.18,  # scale to the entire plot area
                },
            },
            "legend": {
                "orientation": "h",  # "h" for horizontal, "v" for vertical
                "position": {
                    "x": 0.5,  # scale to the entire plot area
                    "y": -0.17,  # scale to the entire plot area
                },
            },
        },
        "trace_config": {
            "mark_size": {
                "lead": 12,  # marker size for the lead fund
                "other": 8,  # marker size for the other funds
            },
            "line_width": {
                "lead": 3,  # line width for the lead fund
                "other": 2,  # line width for the other funds
            },
            "color": {
                "lead": "#C1AE94",
                "other": [
                    "#DACEBF",  # (light teal green)
                    "#989A9C",  # pale warm taupe
                    "#B0AFAF",  # soft cream
                    "#8CA3A0",  # muted sandy tan
                    "#A59BA0",  # off-white with warmth
                    "#7E8B92",  # light stone grey
                    "#E3D8CC",  # pale oat
                    "#C788A5",  # light almond
                ],
            },
            "Y-axis": False,
        },
        "markers": {"enabled": False},  # 👈 no markers for excel style
        "value_boxes": {
            "enabled": True,
            "format": "+.2%",
            "box": {
                "bgcolor": "rgba(255,255,255,0.9)",
                "borderwidth": 1,
                "font_size": 12,
                "xshift": 6,
                "yshift": 6,
            },
            # Show only for lead fund every step and final, for others only final
            "rules": {
                "lead": {"every_step": True, "final": True},
                "others": {"every_step": False, "final": True},
            },
        },
    },
}


def _add_value_boxes(fig, xs, ys, *, indices, color, fmt, boxcfg):
    for i in indices:
        fig.add_annotation(
            x=xs[i],
            y=ys[i],
            text=f"{ys[i]:{fmt}}",
            showarrow=False,
            bgcolor=hex_to_rgba(color, 0.15),  # transparent fill based on trace color
            bordercolor=color,
            borderwidth=boxcfg.get("borderwidth", 1),
            font=dict(size=boxcfg.get("font_size", 12), color=color),
            xanchor="left",
            yanchor="bottom",
            align="center",
            xshift=boxcfg.get("xshift", 6),
            yshift=boxcfg.get("yshift", 6),
            opacity=1.0,
        )


def plot_cumulative_returns(

        funds: Dict[str, Fund], 
        title: str, 
        start_month: str = None, # YYYY-MM
        end_month: str = None, # YYYY-MM
        style: str = "default",
        language: str = "en",  # "en" or "cn"
        blur: bool = False,
        aspect_lock = False,
        custom_ticks = False
    ):


    global_ymin, global_ymax = (0, 0) if custom_ticks else ("","")

    layout_config = STYLE_DICT.get(style, STYLE_DICT["default"])["layout_config"]
    trace_config = STYLE_DICT.get(style, STYLE_DICT["default"])["trace_config"]

    # Convert start and end month to datetime
    start_month = dt.datetime.strptime(start_month, "%Y-%m") if start_month else None
    end_month = dt.datetime.strptime(end_month, "%Y-%m") if end_month else None

    # Collect all months across funds
    start_months = [f.monthly_returns[0]["month"] for f in funds.values()]
    end_months = [f.monthly_returns[-1]["month"] for f in funds.values()]

    # Update start_month and end_month if the current months are not valid
    if not (
        start_month
        and start_month > min(start_months)
        and start_month < max(end_months)
    ):
        start_month = max(start_months)
    if not (end_month and min(start_months) < end_month < max(end_months)):
        end_month = min(end_months)
    # Assert start month is before end month
    if start_month > end_month:
        raise ValueError("start_month must be <= end_month")
    # Get one month before start month
    prev_month = start_month - relativedelta(months=1)  # get previous month

    # If style == excel, change the title
    if style == "excel":
        year = start_month.year
        if language == "en":
            title = f" Performance Since {year}"
        elif language == "cn":
            title = f" 自{year}年至今回报走势"

    # Identify lead fund
    lead_name = "RDGFF" if "RDGFF" in list(funds.keys()) else (list(funds.keys())[0])

    # Initialise figure
    fig = go.Figure()

    # --------------------------- Plot Trace ---------------------------

    # Dict storing final cumulative returns for each fund
    final_cumulative_returns = {}
    value_box_tiers = defaultdict(int)  # index -> next tier number (0,1,2,...)
    STACK_STEP_PX = 16
    color_map: Dict[str, str] = {}
    for index, fund in enumerate(funds.values()):
        # Compute cumulative returns for each month
        months = [
            entry["month"]
            for entry in fund.monthly_returns
            if start_month <= entry["month"] <= end_month
        ]
        cumulative_returns = [
            fund.cumulative_return(start_month=start_month, end_month=entry["month"])
            for entry in fund.monthly_returns
            if start_month <= entry["month"] <= end_month
        ]
        # Add one month before
        months = [prev_month] + months
        cumulative_returns = [0.0] + cumulative_returns
        # Update final_cumulative_returns
        final_cumulative_returns[fund.name] = cumulative_returns[-1]
        is_lead = fund.name == lead_name
        fund_color = (
            trace_config["color"]["lead"]
            if is_lead
            else trace_config["color"]["other"][
                index % len(trace_config["color"]["other"])
            ]
        )

        color_map[fund.name] = fund_color

        # If blur is True, only show the lead fund name as "Fund" or "基金"
        if blur:
            if fund.name == lead_name:
                name = fund_name_map.get("LEAD", {}).get(language, fund.name)
            else:
                name = fund_name_map.get(fund.name, {}).get(language, fund.name)
        else:
            name = fund_name_map.get(fund.name, {}).get(language, fund.name)

        if custom_ticks:
            ymin = np.min(cumulative_returns)
            ymax = np.max(cumulative_returns)
            global_ymax = max(ymax, global_ymax)
            global_ymin = min(ymin, global_ymin)
            # Round down the lower bound, up the upper bound to nearest 10%
            ymin_rounded = np.floor(global_ymin * 10) / 10
            ymax_rounded = np.ceil(global_ymax * 10) / 10

        # Add trace for the fund
        fig.add_trace(
            go.Scatter(
                x=months,
                y=cumulative_returns,
                mode="lines",
                name=name,
                hovertemplate="<b>%{fullData.name}</b><br>%{y:.2%}<extra></extra>",
                line=dict(
                    width=(
                        trace_config["line_width"]["lead"]
                        if fund.name == lead_name
                        else trace_config["line_width"]["other"]
                    ),
                    color=color_map[fund.name],
                    shape="spline",
                    smoothing=0.6,
                ),
            )
        )

        # --- Add annotation ---
        fig.add_annotation(
            xref="paper",
            yref="paper",
            x=1,
            y=-0.2,
            text=(
                "<b>Strictly Confidential</b>"
                if language == "en"
                else "内部信息 严格密保"
            ),
            showarrow=False,
            font=dict(size=9, color="black"),
            xanchor="right",
            yanchor="bottom",
            align="center",
            opacity=0.15,
        )

        step = max(1, int(len(months) / 12))
        marker_indices = list(range(0, len(months), step))
        box_indices = list(range(0, len(months), step))

        # Add markers for every ~10% of the data points
        if (
            STYLE_DICT.get(style, STYLE_DICT["default"])
            .get("markers", {})
            .get("enabled", False)
        ):

            # always include the last point
            if (len(months) - 1) not in marker_indices:
                marker_indices.append(len(months) - 1)

            fig.add_trace(
                go.Scatter(
                    x=[months[i] for i in marker_indices],
                    y=[cumulative_returns[i] for i in marker_indices],
                    mode="markers",
                    showlegend=False,
                    hoverinfo="skip",
                    marker=dict(
                        size=(
                            trace_config["mark_size"]["lead"]
                            if fund.name == lead_name
                            else trace_config["mark_size"]["other"]
                        ),
                        color=color_map[fund.name],
                        line=dict(width=1, color="white")
                    )
                )
            )
            first_last_x = [months[marker_indices[0]], months[marker_indices[-1]]]
            first_last_y = [cumulative_returns[marker_indices[0]], cumulative_returns[marker_indices[-1]]]

            fig.add_trace(
                go.Scatter(
                    x=first_last_x,
                    y=first_last_y,
                    mode="markers",
                    showlegend=False,
                    hoverinfo="skip",
                    marker=dict(
                        size=(trace_config["mark_size"]["lead"] if fund.name == lead_name else trace_config["mark_size"]["other"]) + 4,
                        color=color_map[fund.name],  # hollow center
                        line=dict(width=2, color=color_map[fund.name]),  # outline in series color
                        symbol="circle-open"  # hollow circle marker
                    )
                )
            )




            # Compute a dynamic dtick as 10% of the range
        # Add value boxes based on whether the trace is a lead or not / some style do not need value boxes
        value_box_cfg = STYLE_DICT.get(style, STYLE_DICT["default"]).get(
            "value_boxes", {}
        )
        if value_box_cfg.get("enabled", False):
            rules = value_box_cfg.get(
                "rules",
                {
                    "lead": {"every_step": True, "final": True},
                    "others": {"every_step": False, "final": True},
                },
            )

            idxs = []
            if is_lead and rules["lead"].get("every_step", True):
                # just copy box_indices for lead
                idxs.extend(box_indices)
            elif (not is_lead) and rules["others"].get("every_step", False):
                idxs.extend(box_indices)
            if (is_lead and rules["lead"].get("final", True)) or (
                not is_lead and rules["others"].get("final", True)
            ):
                last = len(months) - 1
                if last not in idxs and idxs:
                    idxs.pop()
                if last not in idxs:
                    idxs.append(last)
            if idxs:
                for ix in sorted(set(idxs)):
                    tier = value_box_tiers[ix]
                    # build a per-index boxcfg that inherits the style, but adds a vertical offset
                    per_index_boxcfg = dict(value_box_cfg.get("box", {}))
                    # Prefer yshift (pixel space). You can also add small xshift if labels are wide.
                    per_index_boxcfg.setdefault("yshift", 0)
                    per_index_boxcfg["yshift"] += tier * STACK_STEP_PX

                    _add_value_boxes(
                        fig,
                        months,
                        cumulative_returns,
                        indices=[
                            ix
                        ],  # draw this index only so we can control offsets per index
                        color=color_map[fund.name],
                        fmt=value_box_cfg.get("format", "+.2%"),
                        boxcfg=per_index_boxcfg,
                    )

                    value_box_tiers[ix] += 1

    # --------------------------- Set Layout ---------------------------

    fig.update_layout(
        title=dict(
            text=f"<b>{title}</b>",
            font=dict(size=32),
            xanchor="center",
            x=0.5,
            yanchor="middle",
            y=0.95,
        ),
        template="plotly_white",
        font=layout_config["font"],
        margin=layout_config["margin"],
        xaxis=dict(
            showgrid=True,
            gridcolor=layout_config["grid_color"],
            tickformat=layout_config["date_ticks"]["format"],
            tickvals=[months[i] for i in box_indices],
            ticktext=[
                months[i].strftime(layout_config["date_ticks"]["format"])
                for i in box_indices
            ],
        ),
        yaxis=dict(
            title="Cumulative Return (%)" if trace_config['Y-axis'] is True else None, 
            tickformat=".0%", 
            showgrid=True,
            gridcolor=layout_config["grid_color"],
            zeroline=True,
            zerolinecolor = layout_config["zero_color"],
            zerolinewidth = 2,
            rangemode = "tozero"
            ),
        legend=dict(
            orientation=layout_config["legend"]["orientation"],
            yanchor="middle",
            y=layout_config["legend"]["position"]["y"],
            xanchor="center",
            x=layout_config["legend"]["position"]["x"],
        ),
        hovermode="x unified",
    )
    # Set up the y-axis to be 1/10 of the max and min of the range
    if custom_ticks:
        custom_ticks = 1/custom_ticks
        dtick = custom_ticks * (ymax_rounded - ymin_rounded)
        fig.update_yaxes(
            dtick = dtick,
            range=[ymin_rounded, ymax_rounded]
        )

    if aspect_lock == True:
        ASPECT_W, ASPECT_H = 13.5, 6
        WIDTH = 1280
        HEIGHT = int(WIDTH * ASPECT_H / ASPECT_W)
        fig.update_layout(width=WIDTH, height=HEIGHT)
        config = dict(
            responsive=False,  # prevents auto-resizing that would change aspect
            toImageButtonOptions=dict(
                format="png",  # or "svg", "jpeg", "webp"
                filename="cumulative_returns",
                width=WIDTH,
                height=HEIGHT,
                scale=2,  # 2x DPI
            ),
            displaylogo=False,
        )

    # --------------------------- Add annotation ---------------------------
    if layout_config["annotation"]["add_annotation"]:
        summary_lines = []

        # Lead (don't use fund.name here!)
        if lead_name in final_cumulative_returns:
            fcr = final_cumulative_returns.pop(
                lead_name
            )  # ok if you don't need it later
            summary_lines.append(
                f"<span style='color:{color_map[lead_name]};'><b>{lead_name}: {fcr:+.2%}</b></span>"
            )

        # Others (use k, not fund.name)
        summary_lines.append(
            " &nbsp; &nbsp; ".join(
                [
                    f"<b><span style='color:{color_map[k]};'>{k}</span>: {v:+.2%}</b>"
                    for k, v in final_cumulative_returns.items()
                ]
            )
        )
        # Add annotations to the figure
        if summary_lines:
            fig.add_annotation(
                xref="paper",
                yref="paper",
                x=layout_config["annotation"]["position"]["x"],
                y=layout_config["annotation"]["position"]["y"],
                showarrow=False,
                text="<br>".join(summary_lines),
            )
    # --------------------------- Show figure ---------------------------
    fig.show()

    return fig


def plot_fund_correlation_heatmap(
    funds: dict,
    *,
    method: str = "pearson",  # "pearson" | "spearman" | "kendall"
    min_overlap: int = 6,  # require at least this many overlapping months for a correlation
    title: str = "Fund Return Correlations",
):
    """
    Build pairwise correlations of monthly returns using pairwise-complete data (max overlap)
    and plot as a heatmap. Returns (fig, corr_df, overlap_df).
    """
    # 1) Assemble a wide DataFrame of monthly returns aligned on month
    series = {}
    for name, f in funds.items():
        s = pd.Series(
            {
                e["month"]: float(e["value"])
                for e in f.monthly_returns
                if e.get("value") is not None
            }
        ).sort_index()
        series[name] = s

    wide = pd.DataFrame(
        series
    )  # index: month; columns: fund names (may contain NaN for missing months)

    # 2) Pairwise overlap counts (n_ij) and correlations with pairwise-complete observations
    mask = wide.notna().astype(int)
    overlap = mask.T @ mask  # n_ij = count of months present in both i and j
    corr = wide.corr(method=method, min_periods=min_overlap)

    # Ensure diagonals look tidy
    for c in corr.columns:
        corr.loc[c, c] = 1.0
        overlap.loc[c, c] = mask[c].sum()

    # 3) Build labels and hover text
    funds_order = list(corr.columns)
    z = corr.loc[funds_order, funds_order].values
    n_pairs = overlap.loc[funds_order, funds_order].values

    text = np.empty_like(z, dtype=object)
    hover = np.empty_like(z, dtype=object)
    for i, rname in enumerate(funds_order):
        for j, cname in enumerate(funds_order):
            r = z[i, j]
            n = int(n_pairs[i, j])
            if pd.isna(r):
                text[i, j] = "–"
                hover[i, j] = f"{rname} × {cname}<br>n = {n}<br>not enough overlap"
            else:
                text[i, j] = f"{r:.2f}\n(n={n})"
                hover[i, j] = f"{rname} × {cname}<br>ρ = {r:.3f}<br>n = {n}"

    # 4) Plotly heatmap (diverging scale, centered at 0)
    fig = go.Figure(
        data=go.Heatmap(
            z=z,
            x=funds_order,
            y=funds_order,
            colorscale="RdBu",
            zmin=-1,
            zmax=1,
            zmid=0,
            colorbar=dict(title="ρ"),
            text=text,
            texttemplate="%{text}",
            hoverinfo="text",
            hovertext=hover,
        )
    )

    # 5) Layout polish
    fig.update_layout(
        title=dict(text=f"<b>{title}</b>", x=0.5, xanchor="center"),
        template="plotly_white",
        font=dict(family="Montserrat, Roboto", size=13, color="#53565A"),
        margin=dict(l=80, r=40, t=80, b=80),
        xaxis=dict(showgrid=False, tickangle=45),
        yaxis=dict(showgrid=False, autorange="reversed"),  # matrix-style orientation
    )

    fig.show()
    return fig, corr, overlap
