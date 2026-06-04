import quixlab as ql

canvas = ql.Canvas(title="My Notebook", lake_tree_open=['ac_telemetry', 'ac_telemetry/environment=prague_office', 'ac_telemetry/environment=prague_office/test_rig=fanatec_csl_dd', 'ac_telemetry/environment=prague_office/test_rig=fanatec_csl_dd/experiment=TestDrive', 'ac_telemetry/environment=prague_office/test_rig=fanatec_csl_dd/experiment=TestDrive/driver=patrick', 'ac_telemetry/environment=prague_office/test_rig=fanatec_csl_dd/experiment=TestDrive/driver=patrick/track=Spa', 'ac_telemetry/environment=prague_office/test_rig=fanatec_csl_dd/experiment=TestDrive/driver=patrick/track=Spa/carModel=lamborghini_huracan_gt3_evo', 'ac_telemetry/environment=prague_office/test_rig=fanatec_csl_dd/experiment=TestDrive/driver=patrick/track=Spa/carModel=lamborghini_huracan_gt3_evo/session_id=2026-06-03T14:11:08.847Z', 'ac_telemetry/environment=prague_office/test_rig=fanatec_csl_dd/experiment=TestDrive/driver=patrick/track=Spa/carModel=lamborghini_huracan_gt3_evo/session_id=2026-06-03T11:39:27.026Z'])


@canvas.dataset(position=(-61, 151), size=(902, 561), code_height=200)
def ac_telemetry():
    return ql.sql("""SELECT *
    FROM ac_telemetry
    WHERE environment = 'prague_office'
      AND test_rig = 'fanatec_csl_dd'
      AND experiment = 'TestDrive'
      AND driver = 'tomas'
      AND track = 'Spa'
      AND carModel = 'lamborghini_huracan_gt3_evo'
      AND session_id = '2026-06-04T13:01:02.619Z'
    ORDER BY timestamp_ms""")


@canvas.cell(position=(1089, 104), size=(831, 578), code_height=200, viz={'type': 'line', 'x': 'timestamp_ms', 'y': ['speedKmh']})
def cell_1(ac_telemetry):
    import pandas as pd

    cols = {}
    for lap, g in ac_telemetry.groupby("lap", sort=True):
        g = g.sort_values("timestamp_ms")
        offset = g["timestamp_ms"] - g["timestamp_ms"].iloc[0]   # zeroed, in ms
        s = pd.Series(g["speedKmh"].values, index=offset.values,
                      name=f"Speed_lap_{lap}")
        cols[lap] = s

    result = pd.concat(cols.values(), axis=1)
    result.index.name = "elapsed_ms"

    cols = {}
    for lap, g in ac_telemetry.groupby("lap", sort=True):
        g = g.sort_values("timestamp_ms")
        offset = g["timestamp_ms"] - g["timestamp_ms"].iloc[0]
        s = pd.Series(g["speedKmh"].values, index=offset.values, name=f"Speed_lap_{lap}")
        cols[lap] = s

    result = pd.concat(cols.values(), axis=1)
    result.index.name = "elapsed_ms"
    result = result.ffill()   
    result = result.reset_index()
    result


@canvas.cell(position=(-714, 194), size=(560, 420), code_height=200)
def cell_2():


if __name__ == "__main__":
    canvas.serve()
