import { describe, it, expect } from "vitest";
import { buildLakehouseQuery } from "@/lib/lakehouse";

const FULL = {
  environment: "prague_office",
  test_rig: "fanatec_csl_dd",
  experiment: "TestDrive",
  driver: "daniel",
  track: "Zandvoort",
  carModel: "porsche_991ii_gt3_r",
};

describe("buildLakehouseQuery", () => {
  it("pins the full partition tuple for a session", () => {
    const q = buildLakehouseQuery(FULL, "2026-06-15T11:50:08.499Z");
    expect(q).toContain("environment = 'prague_office'");
    expect(q).toContain("test_rig = 'fanatec_csl_dd'");
    expect(q).toContain("experiment = 'TestDrive'");
    expect(q).toContain("driver = 'daniel'");
    expect(q).toContain("track = 'Zandvoort'");
    expect(q).toContain("carModel = 'porsche_991ii_gt3_r'");
    expect(q).toContain("session_id = '2026-06-15T11:50:08.499Z'");
    expect(q.trimEnd()).toMatch(/LIMIT 100$/);
  });

  it("omits track/carModel/session_id for a test-level query", () => {
    const q = buildLakehouseQuery(FULL, null);
    expect(q).toContain("driver = 'daniel'");
    expect(q).not.toContain("session_id");
    expect(q).not.toContain("track =");
    expect(q).not.toContain("carModel");
  });

  it("skips null/empty partition fields", () => {
    const q = buildLakehouseQuery(
      { environment: "prague_office", track: null, carModel: null },
      "s1",
    );
    expect(q).toContain("environment = 'prague_office'");
    expect(q).toContain("session_id = 's1'");
    expect(q).not.toContain("track =");
  });

  it("emits no WHERE clause when there are no partition filters", () => {
    const q = buildLakehouseQuery({}, null);
    expect(q).not.toContain("WHERE");
    expect(q).not.toContain("1=1");
    expect(q).toContain("FROM ac_telemetry");
    expect(q.trimEnd()).toMatch(/LIMIT 100$/);
  });

  it("escapes single quotes to avoid breaking the SQL string", () => {
    const q = buildLakehouseQuery({ driver: "o'brien" }, null);
    expect(q).toContain("driver = 'o''brien'");
  });
});
