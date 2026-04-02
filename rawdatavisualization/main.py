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
  

    return (mo,)


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
    r"""
    environment = mo.ui.text(value="prague_office", label="Environment")
      test_rig = mo.ui.text(value="g29", label="Test Rig")
      experiment = mo.ui.text(value="Initial run", label="Experiment")
      driver = mo.ui.text(value="tomas", label="Driver")
      track = mo.ui.text(value="ks_nurburgring", label="Track")
      car_model = mo.ui.text(value="bmw_1m", label="Car Model")
      beers = mo.ui.number(value=0, label="Beers", start=0, stop=20)
      session_id = mo.ui.text(value="2026-03-23T16:08:43.243Z", label="Session ID", full_width=True)
      lap = mo.ui.number(value=1, label="Lap", start=0, stop=999)
      load_btn = mo.ui.run_button(label="Load Data")

      mo.vstack([
          mo.md("## Query Parameters"),
          mo.hstack([environment, test_rig, experiment, driver], justify="start", gap=1),
          mo.hstack([track, car_model, beers, lap], justify="start", gap=1),
          session_id,
          load_btn,
      ])
    """,
    name="_"
)


app._unparsable_cell(
    """
    # TODO: Modify the SQL query for your data
    mo.stop(not load_btn.value, mo.md(\"*Click **Load Data** to fetch telemetry.*\"))

    query = f\"\"\"
      SELECT
          packetId,
          timestamp_ms,
          distanceTraveled,
          speedKmh,
          accG_x, accG_y, accG_z,
          tyreContactPointFL_x, tyreContactPointFL_y, tyreContactPointFL_z,
          tyreContactPointFR_x, tyreContactPointFR_y, tyreContactPointFR_z,
          tyreContactPointRL_x, tyreContactPointRL_y, tyreContactPointRL_z,
          tyreContactPointRR_x, tyreContactPointRR_y, tyreContactPointRR_z
      FROM ac_telemetry
      WHERE environment = '{environment.value}'
        AND test_rig = '{test_rig.value}'
        AND experiment = '{experiment.value}'
        AND driver = '{driver.value}'
        AND track = '{track.value}'
        AND carModel = '{car_model.value}'
        AND beers = {int(beers.value)}
        AND session_id = '{session_id.value}'
        AND lap = {int(lap.value)}
      ORDER BY packetId
      \"\"\"

    df = client.query(query)

      # Car center = average of 4 tyre contact points
    for axis in [\"x\", \"y\", \"z\"]:
      cols = [f\"tyreContactPoint{c}_{axis}\" for c in [\"FL\", \"FR\", \"RL\", \"RR\"]]
      df[f\"car_{axis}\"] = df[cols].mean(axis=1)

    # Time in seconds from lap start
    df[\"time_s\"] = (df[\"timestamp_ms\"] - df[\"timestamp_ms\"].iloc[0]) / 1000.0

    # Distance: use distanceTraveled or compute from positions
    if df[\"distanceTraveled\"].max() == 0:
      dx = df[\"car_x\"].diff().fillna(0)
      dz = df[\"car_z\"].diff().fillna(0)
      df[\"distance_m\"] = np.sqrt(dx**2 + dz**2).cumsum()
    else:
      df[\"distance_m\"] = df[\"distanceTraveled\"]

    mo.md(f\"Loaded **{len(df)}** samples | Duration: **{df['time_s'].iloc[-1]:.1f}s** | Distance:
    **{df['distance_m'].iloc[-1]:.0f}m**\")
    """,
    name="_"
)


app._unparsable_cell(
    r"""
    track_fig = go.Figure()
      track_fig.add_trace(go.Scatter3d(
          x=df["car_x"],
          y=df["car_z"],
          z=df["car_y"],
          mode="lines",
          line=dict(
              color=df["speedKmh"],
              colorscale="Turbo",
              width=4,
              colorbar=dict(title="km/h"),
          ),
      ))
      track_fig.update_layout(
          title="Track Map (colored by speed)",
          scene=dict(
              xaxis_title="X", yaxis_title="Z (forward)", zaxis_title="Y (height)",
              aspectmode="data",
          ),
          height=700,
          margin=dict(l=0, r=0, t=40, b=0),
      )
      mo.ui.plotly(track_fig)
    """,
    name="_"
)


app._unparsable_cell(
    r"""
    x_col = "distance_m" if x_axis.value else "time_s"
    x_label = "Distance (m)" if x_axis.value else "Time (s)"

    speed_fig = go.Figure()
    speed_fig.add_trace(go.Scatter(
      x=df[x_col], y=df["speedKmh"],
      mode="lines", name="Speed",
      line=dict(color="#2196F3", width=1.5),
    ))
    speed_fig.update_layout(
      title="Speed", xaxis_title=x_label, yaxis_title="Speed (km/h)",
      height=400, hovermode="x unified",
    )
    mo.ui.plotly(speed_fig)

    Cell 8 — G-forces plot
    x_col = "distance_m" if x_axis.value else "time_s"
    x_label = "Distance (m)" if x_axis.value else "Time (s)"

    acc_fig = go.Figure()
    for col, color, name in [
      ("accG_x", "#F44336", "Lateral (X)"),
      ("accG_y", "#4CAF50", "Vertical (Y)"),
      ("accG_z", "#FF9800", "Longitudinal (Z)"),
    ]:
      acc_fig.add_trace(go.Scatter(
          x=df[x_col], y=df[col],
          mode="lines", name=name,
          line=dict(color=color, width=1.2),
      ))
    acc_fig.update_layout(
      title="G-Forces", xaxis_title=x_label, yaxis_title="G",
      height=400, hovermode="x unified",
      legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    mo.ui.plotly(acc_fig)
    """,
    name="_"
)


if __name__ == "__main__":
    app.run()
