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
      AND session_id = '2026-06-03T11:08:18.206Z'
    ORDER BY timestamp_ms""")


@canvas.cell(position=(1089, 104), size=(831, 578), code_height=200, viz={'type': 'line', 'x': 'timestamp_ms', 'y': ['speedKmh']})
def cell_1(ac_telemetry):
    return ac_telemetry


@canvas.cell(position=(-714, 194), size=(560, 420), code_height=200)
def cell_2():
    import pandas as pd
    df = pd.DataFrame({"x": [1,2,3], "y": [4,5,6]})
    import plotly.express as px
    return px.bar(df, x="x", y="y")


if __name__ == "__main__":
    canvas.serve()
