# /// script
# [tool.marimo.display]
# theme = "dark"
# ///

import marimo

__generated_with = "0.22.0"
app = marimo.App(width="full")


@app.cell
def _():
    import os
    import marimo as mo
    import pandas as pd
    import plotly.graph_objects as go
    import numpy as np


    return go, mo


@app.cell
def _():
    from quixlake import QuixLakeClient

    return (QuixLakeClient,)


@app.cell
def _(mo):
    mo.md(r"""
    ## Query QuixLake Data
    """)
    return


@app.cell
def _(QuixLakeClient):
    # TODO: Replace with your QuixLake URL
    QUIXLAKE_URL = "https://quixlake-quixers-testrigdemodatawarehouse-prod.az-france-0.app.quix.io"

    client = QuixLakeClient(
        base_url=QUIXLAKE_URL,
        token="pat-6c9b0c84327e40779473f36971c15930"
    )
    return


app._unparsable_cell(
    """
    all_combos = client.query(\"\"\"
              SELECT DISTINCT environment, test_rig, experiment, driver, track, carModel, session_id, lap
              FROM ac_telemetry
              WHERE environment IS NOT NULL
                AND track IS NOT NULL
                AND driver IS NOT NULL
              \"\"\")
          # Keep raw session_id for querying, make display version for dropdown
          all_combos[\"session_id_display\"] = pd.to_datetime(all_combos[\"session_id\"], format=\"mixed\", utc=True).dt.strftime(\"%Y-%m-%d %H:%M:%S\")
    """,
    name="_"
)


@app.cell
def _(all_combos, mo):
    #Cell 3b — Environment
    environment = mo.ui.dropdown(options=sorted(all_combos["environment"].dropna().unique().tolist()), label="Environment")
    environment
    return (environment,)


@app.cell
def _(all_combos, environment, mo):
    #Cell 3c — Test Rig
    _f = all_combos[all_combos["environment"] == environment.value] if environment.value else all_combos
    test_rig = mo.ui.dropdown(options=sorted(_f["test_rig"].dropna().unique().tolist()), label="Test Rig")
    test_rig
    return (test_rig,)


@app.cell
def _(all_combos, environment, mo, test_rig):
    #Cell 3d — Experiment
    _f = all_combos[(all_combos["environment"] == environment.value) & (all_combos["test_rig"] == test_rig.value)] if environment.value and test_rig.value else all_combos
    experiment = mo.ui.dropdown(options=sorted(_f["experiment"].dropna().unique().tolist()), label="Experiment")
    experiment
    return (experiment,)


@app.cell
def _(all_combos, environment, experiment, mo, test_rig):
    #Cell 3e — Driver
    _f = all_combos.copy()
    if environment.value: _f = _f[_f["environment"] == environment.value]
    if test_rig.value: _f = _f[_f["test_rig"] == test_rig.value]
    if experiment.value: _f = _f[_f["experiment"] == experiment.value]
    driver = mo.ui.dropdown(options=sorted(_f["driver"].dropna().unique().tolist()), label="Driver")
    driver
    return (driver,)


@app.cell
def _(all_combos, driver, environment, experiment, mo, test_rig):
    #Cell 3f — Track + Car Model
    _f = all_combos.copy()
    if environment.value: _f = _f[_f["environment"] == environment.value]
    if test_rig.value: _f = _f[_f["test_rig"] == test_rig.value]
    if experiment.value: _f = _f[_f["experiment"] == experiment.value]
    if driver.value: _f = _f[_f["driver"] == driver.value]

    track = mo.ui.dropdown(options=sorted(_f["track"].dropna().unique().tolist()), label="Track")
    car_model = mo.ui.dropdown(options=sorted(_f["carModel"].dropna().unique().tolist()), label="Car Model")

    mo.hstack([track, car_model], justify="start", gap=1)
    return


app._unparsable_cell(
    r"""
    #Cell 3g — Session + Lap + Load
     _f = all_combos.copy()
    if environment.value: _f = _f[_f["environment"] == environment.value]
    if test_rig.value: _f = _f[_f["test_rig"] == test_rig.value]
    if experiment.value: _f = _f[_f["experiment"] == experiment.value]
    if driver.value: _f = _f[_f["driver"] == driver.value]
    if track.value: _f = _f[_f["track"] == track.value]
    if car_model.value: _f = _f[_f["carModel"] == car_model.value]

    # Map display -> raw session_id
    session_map = dict(zip(_f["session_id_display"].astype(str), _f["session_id"].astype(str)))
    session_id = mo.ui.dropdown(options=sorted(session_map.keys()), label="Session ID")
    lap_opts = [str(l) for l in sorted(_f["lap"].dropna().unique().tolist())]
    lap = mo.ui.dropdown(options=lap_opts, label="Lap")
    load_btn = mo.ui.run_button(label="Load Data")

    mo.hstack([session_id, lap, load_btn], justify="start", gap=1)
    """,
    name="_"
)


app._unparsable_cell(
    """
    # TODO: Modify the SQL query for your data
     mo.stop(not load_btn.value, mo.md(\"*Click **Load Data** to fetch telemetry.*\"))

    raw_session_id = session_map.get(session_id.value, session_id.value)

    query = f\"\"\"
    SELECT *
    FROM ac_telemetry
    WHERE environment = '{environment.value}'
      AND test_rig = '{test_rig.value}'
      AND experiment = '{experiment.value}'
      AND driver = '{driver.value}'
      AND track = '{track.value}'
      AND carModel = '{car_model.value}'
      AND session_id = '{raw_session_id}'
      AND lap = {int(lap.value)}
    ORDER BY packetId
    \"\"\"

    raw = client.query(query)

    keep = [
    \"packetId\", \"timestamp_ms\", \"distanceTraveled\", \"speedKmh\",
    \"accG_x\", \"accG_y\", \"accG_z\",
    \"tyreContactPointFL_x\", \"tyreContactPointFL_y\", \"tyreContactPointFL_z\",
    \"tyreContactPointFR_x\", \"tyreContactPointFR_y\", \"tyreContactPointFR_z\",
    \"tyreContactPointRL_x\", \"tyreContactPointRL_y\", \"tyreContactPointRL_z\",
    \"tyreContactPointRR_x\", \"tyreContactPointRR_y\", \"tyreContactPointRR_z\",
    ]
    df = raw[[c for c in keep if c in raw.columns]].copy()

    for axis in [\"x\", \"y\", \"z\"]:
    cols = [f\"tyreContactPoint{c}_{axis}\" for c in [\"FL\", \"FR\", \"RL\", \"RR\"]]
    df[f\"car_{axis}\"] = df[cols].mean(axis=1)

    df[\"time_s\"] = (df[\"timestamp_ms\"] - df[\"timestamp_ms\"].iloc[0]) / 1000.0

    if df[\"distanceTraveled\"].max() == 0:
    dx = df[\"car_x\"].diff().fillna(0)
    dz = df[\"car_z\"].diff().fillna(0)
    df[\"distance_m\"] = np.sqrt(dx**2 + dz**2).cumsum()
    else:
    df[\"distance_m\"] = df[\"distanceTraveled\"]

    mo.md(f\"Loaded **{len(df)}** samples | Duration: **{df['time_s'].iloc[-1]:.1f}s** | Distance: **{df['distance_m'].iloc[-1]:.0f}m**\")



    """,
    name="_"
)


@app.cell
def _(mo):
    x_axis = mo.ui.switch(label="Time (off) / Distance (on)", value=False)
    mo.md(f"### X-axis mode: {x_axis}")
    return (x_axis,)


@app.cell
def _(df, go, mo):
    track_fig = go.Figure()
    track_fig.add_trace(go.Scatter3d(
      x=df["car_z"],
      y=df["car_x"],
      z=df["car_y"],
      mode="lines",
      line=dict(
          color=df["speedKmh"],
          colorscale="Turbo",
          width=4,
          colorbar=dict(title="km/h"),
      ),
    ))

    pad = 0.05
    x_range = [df["car_z"].min(), df["car_z"].max()]
    y_range = [df["car_x"].min(), df["car_x"].max()]
    z_range = [df["car_y"].min(), df["car_y"].max()]
    x_pad = (x_range[1] - x_range[0]) * pad
    y_pad = (y_range[1] - y_range[0]) * pad
    z_pad = (z_range[1] - z_range[0]) * pad

    track_fig.update_layout(
      title="Track Map (colored by speed)",
      scene=dict(
          xaxis=dict(title="X [m]", range=[x_range[0] - x_pad, x_range[1] + x_pad]),
          yaxis=dict(title="Y [m]", range=[y_range[0] - y_pad, y_range[1] + y_pad]),
          zaxis=dict(title="Z [m]", range=[z_range[0] - z_pad, z_range[1] + z_pad], showticklabels=False),
          aspectmode="data",
      ),
      height=700,
      margin=dict(l=0, r=0, t=40, b=0),
    )
    mo.ui.plotly(track_fig)
    return


@app.cell
def _(df, go, mo, x_axis):
    x_col = "distance_m" if x_axis.value else "time_s"
    x_label = "Distance [m]" if x_axis.value else "Time [s]"

    speed_fig = go.Figure()
    speed_fig.add_trace(go.Scatter(
      x=df[x_col], y=df["speedKmh"],
      mode="lines", name="Speed",
      line=dict(color="#2196F3", width=1.5),
    ))
    speed_fig.update_layout(
      title="Speed", xaxis_title=x_label, yaxis_title="Speed [km/h]",
      height=400, hovermode="x unified",
    )
    mo.ui.plotly(speed_fig)
    return


@app.cell
def _(df, go, mo, x_axis):
    #Cell 8a — Lateral G (accG_x)
    x_colAccX = "distance_m" if x_axis.value else "time_s"
    x_labelAccX = "Distance [m]" if x_axis.value else "Time [s]"

    fig_gx = go.Figure()
    fig_gx.add_trace(go.Scatter(
      x=df[x_colAccX], y=df["accG_x"],
      mode="lines", name="Lateral (X)",
      line=dict(color="#F44336", width=1.2),
    ))
    fig_gx.update_layout(
      title="Lateral G (X)", xaxis_title=x_labelAccX, yaxis_title="G",
      height=350, hovermode="x unified",
    )
    mo.ui.plotly(fig_gx)
    return


@app.cell
def _(df, go, mo, x_axis):
    #Cell 8b — Vertical G (accG_y)
    x_colAccY = "distance_m" if x_axis.value else "time_s"
    x_labelAccY = "Distance [m]" if x_axis.value else "Time [s]"

    fig_gy = go.Figure()
    fig_gy.add_trace(go.Scatter(
      x=df[x_colAccY], y=df["accG_y"],
      mode="lines", name="Vertical (Y)",
      line=dict(color="#4CAF50", width=1.2),
    ))
    fig_gy.update_layout(
      title="Vertical G (Y)", xaxis_title=x_labelAccY, yaxis_title="G",
      height=350, hovermode="x unified",
    )
    mo.ui.plotly(fig_gy)
    return


@app.cell
def _(df, go, mo, x_axis):
    #Cell 8c — Longitudinal G (accG_z)
    x_colAccZ = "distance_m" if x_axis.value else "time_s"
    x_labelAccZ = "Distance [m]" if x_axis.value else "Time [s]"

    fig_gz = go.Figure()
    fig_gz.add_trace(go.Scatter(
      x=df[x_colAccZ], y=df["accG_z"],
      mode="lines", name="Longitudinal (Z)",
      line=dict(color="#FF9800", width=1.2),
    ))
    fig_gz.update_layout(
      title="Longitudinal G (Z)", xaxis_title=x_labelAccZ, yaxis_title="G",
      height=350, hovermode="x unified",
    )
    mo.ui.plotly(fig_gz)
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
