# AC Telemetry Channels

Columns available in the `ac_telemetry` table in Quix Lakehouse, grouped by category.

> **Data source: Assetto Corsa Competizione (ACC).** ACC's channel set is a
> *superset* of the original Assetto Corsa channels listed below — every name
> here is present and valid. ACC adds extras not enumerated here, e.g. weather
> (`rainIntensity`, `rainIntensityIn10min`, `rainTyres`), tyre-set MFD
> (`mfdTyrePressureLF`, `currentTyreSet`, `strategyTyreSet`), brake compounds
> (`frontBrakeCompound`, `rearBrakeCompound`), ERS/DRS (`ersPowerLevel`,
> `drsAvailable`), and gaps (`gapAhead`, `gapBehind`). If a column you need
> isn't listed below, call `get_schema` to confirm it exists.

Column naming conventions:

- Per-wheel columns use suffixes `FL`, `FR`, `RL`, `RR` (front-left, front-right, rear-left, rear-right).
- Per-axis columns use suffixes `_x`, `_y`, `_z` (world-frame unless noted).
- `normalizedCarPosition` ranges 0 → 1 over one lap.

## Channels by category

### Inputs

| Column | Label | Unit |
|---|---|---|
| `gas` | Throttle | [-] |
| `brake` | Brake | [-] |
| `clutch` | Clutch | [-] |
| `gear` | Gear | [-] |
| `steerAngle` | Steering Angle | [rad] |

### Motion

| Column | Label | Unit |
|---|---|---|
| `speedKmh` | Speed | [km/h] |
| `velocity_x` | Velocity X | [m/s] |
| `velocity_y` | Velocity Y | [m/s] |
| `velocity_z` | Velocity Z | [m/s] |
| `localVelocity_x` | Local Velocity X | [m/s] |
| `localVelocity_y` | Local Velocity Y | [m/s] |
| `localVelocity_z` | Local Velocity Z | [m/s] |
| `accG_x` | G-Force Lateral | [g] |
| `accG_y` | G-Force Vertical | [g] |
| `accG_z` | G-Force Longitudinal | [g] |
| `localAngularVel_x` | Angular Velocity X | [rad/s] |
| `localAngularVel_y` | Angular Velocity Y | [rad/s] |
| `localAngularVel_z` | Angular Velocity Z | [rad/s] |
| `heading` | Heading | [rad] |
| `pitch` | Pitch | [rad] |
| `roll` | Roll | [rad] |

### Engine

| Column | Label | Unit |
|---|---|---|
| `rpms` | Engine Speed | [rpm] |
| `turboBoost` | Turbo Boost | [-] |
| `fuel` | Fuel | [L] |
| `engineBrake` | Engine Brake | [-] |
| `drs` | DRS Active | [-] |
| `drsAvailable` | DRS Available | [-] |
| `drsEnabled` | DRS Enabled | [-] |
| `kersCharge` | KERS/ERS Charge | [-] |
| `kersInput` | KERS/ERS Input | [-] |
| `kersCurrentKJ` | KERS Energy Spent | [kJ] |
| `ersRecoveryLevel` | ERS Recovery Level | [-] |
| `ersPowerLevel` | ERS Power Level | [-] |
| `ersHeatCharging` | ERS Heat Charging | [-] |
| `ersIsCharging` | ERS Charging | [-] |

### Tyres

| Column | Label | Unit |
|---|---|---|
| `tyreTempFL` | Tyre Temp Core FL | [°C] |
| `tyreTempFR` | Tyre Temp Core FR | [°C] |
| `tyreTempRL` | Tyre Temp Core RL | [°C] |
| `tyreTempRR` | Tyre Temp Core RR | [°C] |
| `tyreTempIFL` | Tyre Temp Inner FL | [°C] |
| `tyreTempIFR` | Tyre Temp Inner FR | [°C] |
| `tyreTempIRL` | Tyre Temp Inner RL | [°C] |
| `tyreTempIRR` | Tyre Temp Inner RR | [°C] |
| `tyreTempMFL` | Tyre Temp Middle FL | [°C] |
| `tyreTempMFR` | Tyre Temp Middle FR | [°C] |
| `tyreTempMRL` | Tyre Temp Middle RL | [°C] |
| `tyreTempMRR` | Tyre Temp Middle RR | [°C] |
| `tyreTempOFL` | Tyre Temp Outer FL | [°C] |
| `tyreTempOFR` | Tyre Temp Outer FR | [°C] |
| `tyreTempORL` | Tyre Temp Outer RL | [°C] |
| `tyreTempORR` | Tyre Temp Outer RR | [°C] |
| `wheelsPressureFL` | Tyre Pressure FL | [psi] |
| `wheelsPressureFR` | Tyre Pressure FR | [psi] |
| `wheelsPressureRL` | Tyre Pressure RL | [psi] |
| `wheelsPressureRR` | Tyre Pressure RR | [psi] |
| `tyreWearFL` | Tyre Wear FL | [-] |
| `tyreWearFR` | Tyre Wear FR | [-] |
| `tyreWearRL` | Tyre Wear RL | [-] |
| `tyreWearRR` | Tyre Wear RR | [-] |
| `wheelSlipFL` | Wheel Slip FL | [-] |
| `wheelSlipFR` | Wheel Slip FR | [-] |
| `wheelSlipRL` | Wheel Slip RL | [-] |
| `wheelSlipRR` | Wheel Slip RR | [-] |
| `wheelLoadFL` | Wheel Load FL | [N] |
| `wheelLoadFR` | Wheel Load FR | [N] |
| `wheelLoadRL` | Wheel Load RL | [N] |
| `wheelLoadRR` | Wheel Load RR | [N] |
| `wheelAngularSpeedFL` | Wheel Angular Speed FL | [rad/s] |
| `wheelAngularSpeedFR` | Wheel Angular Speed FR | [rad/s] |
| `wheelAngularSpeedRL` | Wheel Angular Speed RL | [rad/s] |
| `wheelAngularSpeedRR` | Wheel Angular Speed RR | [rad/s] |
| `tyreDirtyLevelFL` | Tyre Dirty Level FL | [-] |
| `tyreDirtyLevelFR` | Tyre Dirty Level FR | [-] |
| `tyreDirtyLevelRL` | Tyre Dirty Level RL | [-] |
| `tyreDirtyLevelRR` | Tyre Dirty Level RR | [-] |
| `camberRADFL` | Camber FL | [rad] |
| `camberRADFR` | Camber FR | [rad] |
| `camberRADRL` | Camber RL | [rad] |
| `camberRADRR` | Camber RR | [rad] |
| `tyreContactPointFL_x` | Tyre Contact FL X | [m] |
| `tyreContactPointFL_y` | Tyre Contact FL Y | [m] |
| `tyreContactPointFL_z` | Tyre Contact FL Z | [m] |
| `tyreContactPointFR_x` | Tyre Contact FR X | [m] |
| `tyreContactPointFR_y` | Tyre Contact FR Y | [m] |
| `tyreContactPointFR_z` | Tyre Contact FR Z | [m] |
| `tyreContactPointRL_x` | Tyre Contact RL X | [m] |
| `tyreContactPointRL_y` | Tyre Contact RL Y | [m] |
| `tyreContactPointRL_z` | Tyre Contact RL Z | [m] |
| `tyreContactPointRR_x` | Tyre Contact RR X | [m] |
| `tyreContactPointRR_y` | Tyre Contact RR Y | [m] |
| `tyreContactPointRR_z` | Tyre Contact RR Z | [m] |
| `tyreContactNormalFL_x` | Tyre Normal FL X | [-] |
| `tyreContactNormalFL_y` | Tyre Normal FL Y | [-] |
| `tyreContactNormalFL_z` | Tyre Normal FL Z | [-] |
| `tyreContactNormalFR_x` | Tyre Normal FR X | [-] |
| `tyreContactNormalFR_y` | Tyre Normal FR Y | [-] |
| `tyreContactNormalFR_z` | Tyre Normal FR Z | [-] |
| `tyreContactNormalRL_x` | Tyre Normal RL X | [-] |
| `tyreContactNormalRL_y` | Tyre Normal RL Y | [-] |
| `tyreContactNormalRL_z` | Tyre Normal RL Z | [-] |
| `tyreContactNormalRR_x` | Tyre Normal RR X | [-] |
| `tyreContactNormalRR_y` | Tyre Normal RR Y | [-] |
| `tyreContactNormalRR_z` | Tyre Normal RR Z | [-] |
| `tyreContactHeadingFL_x` | Tyre Heading FL X | [-] |
| `tyreContactHeadingFL_y` | Tyre Heading FL Y | [-] |
| `tyreContactHeadingFL_z` | Tyre Heading FL Z | [-] |
| `tyreContactHeadingFR_x` | Tyre Heading FR X | [-] |
| `tyreContactHeadingFR_y` | Tyre Heading FR Y | [-] |
| `tyreContactHeadingFR_z` | Tyre Heading FR Z | [-] |
| `tyreContactHeadingRL_x` | Tyre Heading RL X | [-] |
| `tyreContactHeadingRL_y` | Tyre Heading RL Y | [-] |
| `tyreContactHeadingRL_z` | Tyre Heading RL Z | [-] |
| `tyreContactHeadingRR_x` | Tyre Heading RR X | [-] |
| `tyreContactHeadingRR_y` | Tyre Heading RR Y | [-] |
| `tyreContactHeadingRR_z` | Tyre Heading RR Z | [-] |

### Suspension & Brakes

| Column | Label | Unit |
|---|---|---|
| `brakeTempFL` | Brake Temp FL | [°C] |
| `brakeTempFR` | Brake Temp FR | [°C] |
| `brakeTempRL` | Brake Temp RL | [°C] |
| `brakeTempRR` | Brake Temp RR | [°C] |
| `brakeBias` | Brake Bias | [-] |
| `suspensionTravelFL` | Suspension Travel FL | [m] |
| `suspensionTravelFR` | Suspension Travel FR | [m] |
| `suspensionTravelRL` | Suspension Travel RL | [m] |
| `suspensionTravelRR` | Suspension Travel RR | [m] |
| `rideHeightFront` | Ride Height Front | [m] |
| `rideHeightRear` | Ride Height Rear | [m] |
| `cgHeight` | CoG Height | [m] |

### Environment

| Column | Label | Unit |
|---|---|---|
| `airTemp` | Air Temperature | [°C] |
| `roadTemp` | Road Temperature | [°C] |
| `airDensity` | Air Density | [kg/m³] |
| `surfaceGrip` | Surface Grip | [-] |

### Car State

| Column | Label | Unit |
|---|---|---|
| `carDamage_front` | Damage Front | [-] |
| `carDamage_rear` | Damage Rear | [-] |
| `carDamage_left` | Damage Left | [-] |
| `carDamage_right` | Damage Right | [-] |
| `carDamage_top` | Damage Top | [-] |
| `ballast` | Ballast | [kg] |
| `tc` | TC Slip Limit | [-] |
| `abs` | ABS Slip Limit | [-] |
| `pitLimiterOn` | Pit Limiter | [-] |
| `autoShifterOn` | Auto Shifter | [-] |
| `isAIControlled` | AI Controlled | [-] |
| `finalFF` | Force Feedback | [-] |
| `numberOfTyresOut` | Tyres Out of Track | [-] |

### Session

| Column | Label | Unit |
|---|---|---|
| `normalizedCarPosition` | Track Position | [-] |
| `completedLaps` | Completed Laps | [-] |
| `position` | Position | [-] |
| `iCurrentTime` | Current Lap Time | [ms] |
| `iLastTime` | Last Lap Time | [ms] |
| `iBestTime` | Best Lap Time | [ms] |
| `sessionTimeLeft` | Session Time Left | [s] |
| `distanceTraveled` | Distance Traveled | [m] |
| `isInPit` | In Pit | [-] |
| `isInPitLane` | In Pit Lane | [-] |
| `currentSectorIndex` | Current Sector | [-] |
| `lastSectorTime` | Last Sector Time | [ms] |
| `numberOfLaps` | Number of Laps | [-] |
| `performanceMeter` | Performance Delta | [-] |
| `penaltyTime` | Penalty Time | [s] |
| `mandatoryPitDone` | Mandatory Pit Done | [-] |
| `carCoordinates_x` | Car Position X | [m] |
| `carCoordinates_y` | Car Position Y | [m] |
| `carCoordinates_z` | Car Position Z | [m] |
| `replayTimeMultiplier` | Replay Speed | [-] |
| `packetId` | Packet ID | [-] |

