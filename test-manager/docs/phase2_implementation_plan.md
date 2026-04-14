# Phase 2 Implementation Plan - Test Manager System

**Author:** Patrick Mira
**Date:** 2025-10-14
**Status:** Planning
**Source Document:** [domain_model_requirements.md](./domain_model_requirements.md)

---

## Current State Analysis

**Phase 1 (Current):**
- Simple Test entity with single `sample_id` (string) and `environment_id` (string)
- Logbook entries tied to Tests
- File attachments and links
- Basic CRUD operations

**Phase 2 (Target):**
- Device as first-class entities
- Tests can reference **multiple Devices** with versioning
- Environment (Test Environment) as separate entity
- Comprehensive journaling for Devices
- Safety Requirements system
- Product hierarchy and lookup tables
- Refrigerant management

---

## Recommended Implementation Approach

We recommend implementing Phase 2 in **3 sub-phases** to show the customer incremental progress and allow them to learn the remaining features:

---

## Sub-Phase 2.1 - Core Device & Test Restructuring (MVP)

**Priority:** Implement first - Shows immediate value

### Backend Tasks:

1. **✅ Create Device Models** (`backend/api/models.py`) - **COMPLETED**
   - `DeviceStatus` enum (created, setup, stored, scrapped)
   - `Refrigerant` embedded model
   - `Device` model with all fields from requirements
   - `DeviceCreate` and `DeviceUpdate` models
   - `DeviceQuery` model for filtering

2. **✅ Create DeviceJournalEntry Models** - **COMPLETED**
   - `JournalCategory` enum
   - `DeviceJournalEntry` model
   - `DeviceJournalEntryCreate` model

3. **✅ Create Device Routes** (`backend/api/routes/devices.py`) - **COMPLETED**
   - `POST /devices` - Create Device (auto-generate first journal entry)
   - `GET /devices` - List Devices with filtering
   - `GET /devices/{device_id}` - Get single Device
   - `PUT /devices/{device_id}` - Update Device (auto-generate journal entry)
   - `DELETE /devices/{device_id}` - Delete Device
   - `GET /devices/{device_id}/journal` - Get Device journal history
   - `POST /devices/{device_id}/journal` - Create manual journal entry

4. **✅ Create Lookup Tables** (Basic) - **COMPLETED**
   - `SampleTypes` model and seed data
   - `Location` model and seed data
   - Routes: `GET /lookups/sample-types`, `GET /lookups/locations`

5. **✅ Update Test Model** - **COMPLETED**
   - Change `sample_id: str` → `devices: list[DeviceReference]` where `DeviceReference = {device_id, device_version}`
   - Change `environment_id: str` → `environment_id: str` and add `environment_version: uuid`
   - Update `TestCreate` and `TestUpdate` models

6. **✅ Update Test Routes** - **COMPLETED**
   - Modify create/update logic to handle `devices` array
   - When Test transitions to `in_progress`, capture latest `device_version` for each Device

7. **✅ Database Migration Script** - **COMPLETED**
   - Convert existing tests from `sample_id` (string) to `devices` array format
   - Seed initial lookup tables (sample_types, locations)
   - Complete migration system with dry-run, execute, and rollback modes
   - Helper scripts for easy execution
   - Comprehensive documentation and testing
   - Location: `migrations/phase1_to_phase2/`

### Frontend Tasks:

> **Note**: The frontend was migrated from Flet (Python) to Next.js (TypeScript/React). The file paths below reference the old Flet structure and are kept for historical reference. The current Next.js frontend is located in `frontend/` with TypeScript types in `frontend/types/`.

8. **✅ Create Device Models** (`frontend/app/models.py` - obsolete Flet path) - **COMPLETED**
   - `DeviceStatus` enum with label properties
   - `JournalCategory` enum
   - `Refrigerant` dataclass
   - `Device` dataclass with all fields
   - `DeviceJournalEntry` dataclass
   - `DeviceReference` dataclass
   - `SampleType` and `Location` lookup models
   - Updated `Test` model with `devices: list[DeviceReference]`, `environment_id`, `environment_version`

9. **✅ Store Methods** (`frontend/app/store.py`) - **COMPLETED**
   - Updated `get_tests()` and `get_test()` for Device array handling
   - Updated `add_test()` signature for multi-Device support
   - Added Device methods: `get_devices()`, `get_device()`, `create_device()`, `update_device()`, `delete_device()`
   - Added journal methods: `get_device_journal()`, `create_device_journal_entry()`
   - Added lookup methods: `get_sample_types()`, `get_locations()`

10. **✅ Create Device Components** (`frontend/app/components/devices/`) - **COMPLETED**
    - `DeviceStatusBadge` - Status badge with color mapping
    - `DevicesFilter` - Filter component for Device list (92 lines)
    - `DevicesTable` - Table with View/Edit/Delete actions (107 lines)
    - `JournalTimeline` - Timeline view with manual entry form (106 lines)
    - `DeviceForm` - Comprehensive form with sections (339 lines)
    - All components follow established patterns from Test components

11. **✅ Create Device Views** (`frontend/app/views.py`) - **COMPLETED**
    - `devices_list_view()` - List view with filtering (/devices)
    - `device_add_view()` - Add new Device with form (/devices/add)
    - `device_detail_view()` - Detail view with journal timeline (/devices/:device_id)
    - `device_edit_view()` - Edit existing Device (/devices/:device_id/edit)
    - Total: 377 lines of view code

12. **✅ Update Test Views for Multi-Device** - **COMPLETED**
    - Created `DevicePicker` component - Multi-select Device picker
    - Updated `test_add_view()` - Device picker instead of sample_id, environment_id instead of environment_id
    - Updated `test_detail_view()` - Display Device list with links, show Environment ID and version
    - Updated `TestsTable` - Changed columns to show Environment ID and Devices (first 2 + count)
    - Updated `TestsFilter` - Filter by device_id with special array handling
    - Updated `_filter_tests()` - Special handling for device_id in array

### Validation Rules (MVP):
- Derive `sample_id` from `sample_type` and `sample_nr`
- Basic refrigerant validation (if `circuit_ready=true`, require `medium` and `amount_kg`)

**Outcome:** Customer can manage Devices independently and link multiple Devices to Tests.

---

## Sub-Phase 2.2 - Product Hierarchy & Safety Requirements

**Priority:** Customer implements with our support - Applies patterns from 2.1

### Backend Tasks:

13. **Create Product Lookup Tables**
    - `ProductCategory` model (with `requires_refrigerant` flag)
    - `Product` model (manufacturer, category, product_name)
    - Seed data for products
    - Routes: `GET /lookups/product-categories`, `GET /lookups/products` (with filtering by manufacturer/category)

14. **Create RefrigerantMedia Lookup**
    - `RefrigerantMedia` model
    - Seed data (R32, R410A, R134a, R290, R744)
    - Route: `GET /lookups/refrigerant-media`

15. **Create Safety Requirements Models**
    - `SafetyRequirementTemplate` model
    - `SafetyRequirementResult` model
    - `SafetyRequirementResultCreate` model

16. **Create Safety Requirements Routes** (`backend/api/routes/safety_requirements.py`)
    - `GET /safety-requirements/templates` - List templates (filter by product_category)
    - `GET /devices/{device_id}/safety-requirements` - Get all results for a Device
    - `POST /devices/{device_id}/safety-requirements` - Create/update safety result
    - `PUT /devices/{device_id}/safety-requirements/{result_id}` - Update result

17. **Update Device Model**
    - Add calculated fields: `attended_operation` and `unattended_operation` (computed from safety requirements)

18. **Enhanced Validation**
    - Check `ProductCategory.requires_refrigerant` flag
    - Enforce refrigerant fields based on product category
    - Validate immutable fields on Device updates

### Frontend Tasks:

19. **Update Device Form**
    - Cascading dropdowns: Manufacturer → Category → Product
    - Auto-suggest Type/Variant/Key from existing Devices
    - Refrigerant section shown/hidden based on `ProductCategory.requires_refrigerant`

20. **Create Safety Requirements View**
    - Checklist UI showing all templates for Device's product category
    - Mark each requirement as passed/failed
    - Add comments and links to each check
    - Show `attended_operation` and `unattended_operation` status badges

**Outcome:** Full product hierarchy, refrigerant management, and safety requirements tracking.

**Note for Customer:** This sub-phase follows the same patterns established in Sub-Phase 2.1. Use Claude Code CLI with the implementation plan, following the task grouping approach we demonstrated. We will conduct online sessions where you implement with our instructions and support.

---

## Sub-Phase 2.3 - Environment Management (Requirements Definition + Implementation)

**Priority:** Customer independently defines requirements and implements - Complete development cycle

**Important:** Environment is currently defined as "**out of scope for phase 2 but referenced**" and a "**placeholder**" in the domain model. Before implementation, the customer must first **define the full Environment requirements**.

### Phase 1: Requirements Definition

**Customer Task:** Update `docs/domain_model_requirements.md` to fully specify Environment entity:

- [ ] Define all Environment fields (beyond the basic `_id`, `name`, `environment_id`, `location`)
- [ ] Define Environment status values if applicable
- [ ] Define immutable vs mutable fields
- [ ] Specify validation rules
- [ ] Define Environment-specific business rules
- [ ] Specify how Environment relates to Tests and Devices
- [ ] Define what data should be captured in EnvironmentJournalEntry snapshots
- [ ] Document Environment lifecycle and workflows

**Questions to Answer:**
- What metadata does an Environment need (sensors, capabilities, configurations)?
- How are Environments commissioned/decommissioned?
- What changes to Environment require journal entries?
- How should Environment location relate to Device locations?
- Are there safety requirements or validations for Environments?

### Phase 2: Implementation

Once requirements are defined, implement following the Device pattern:

#### Backend Tasks:

21. **Create Environment Models** (based on finalized requirements)
    - `Environment` model with all defined fields
    - `EnvironmentJournalEntry` model (for versioning)
    - `EnvironmentCreate`, `EnvironmentUpdate`, `EnvironmentQuery` models

22. **Create Environment Routes** (`backend/api/routes/environment.py`)
    - CRUD endpoints (following Device pattern)
    - Journal management (auto-generation + manual entries)
    - Validation logic per requirements

23. **Update Test Logic**
    - When Test starts, capture `environment_version` from latest EnvironmentJournalEntry
    - Implement location validation if defined in requirements

24. **Create Environment Tests**
    - Unit tests for Environment CRUD
    - Journal versioning tests
    - Validation tests per requirements

#### Frontend Tasks:

> **Note**: File paths reference obsolete Flet structure. Current Next.js frontend uses TypeScript types in `frontend/types/`.

25. **Create Environment Models** (`frontend/types/` - Next.js TypeScript types)
    - Mirror backend Environment models

26. **Create Environment Views**
    - Environment list view with filters
    - Environment detail/edit form
    - Environment journal timeline view

27. **Enhance Test Form**
    - Environment picker dropdown
    - Location validation/warnings per requirements

### Documentation We Provide:

28. **Requirements Template** for Environment
    - Template section for docs/domain_model_requirements.md
    - Examples of well-defined requirements from Device
    - Checklist of what to define

29. **Implementation Guide**
    - How to use Claude Code to implement from updated requirements
    - Reference to Device implementation patterns
    - Testing checklist

**Outcome:** Customer learns the **complete development cycle** - from requirements definition through implementation. Demonstrates ability to extend the system independently.

---

## Database Seeds & Admin Setup

27. **Seed Data Scripts** (`mongodb/seeds/`)
    - Sample types (PFP, FP, A, B, etc.)
    - Locations (Bench 3, Site A, Lab 2, etc.)
    - Product categories (Gas, WP, PV, Air, Oil)
    - Products (sample manufacturer/category/product combinations)
    - Refrigerant media (R32, R410A, etc.)
    - Safety requirement templates (per product category)

---

## Testing Strategy

28. **Backend Tests**
    - Unit tests for each new route (`backend/tests/test_devices.py`, etc.)
    - Validation logic tests
    - Journal versioning tests

29. **Integration Tests**
    - Test → Device relationship tests
    - Multi-Device test scenarios
    - Safety requirements workflow tests

---

## Summary of Customer Learning Path

### Sub-Phase 2.1 (We implement)
**Customer learns by observing:**
- How to use Claude Code CLI with the implementation plan
- Coding patterns and conventions
- How requirements translate to implementation
- Testing and validation approaches

### Sub-Phase 2.2 (Customer implements with our support)
**Customer applies learned patterns:**
- Implements product hierarchy and safety requirements in online sessions with our instructions
- Uses Claude Code CLI hands-on
- Follows established Device patterns
- We provide real-time support during sessions

### Sub-Phase 2.3 (Customer independently)
**Customer demonstrates full capability:**
- Defines Environment requirements in the domain model first
- Implements Environment following established patterns
- Completes the full development cycle
- Extends the system independently

This approach allows us to:
- ✅ Deliver core value quickly (Sub-Phase 2.1)
- ✅ Enable customer hands-on learning with guidance (Sub-Phase 2.2)
- ✅ Customer demonstrates full independent capability (Sub-Phase 2.3)
- ✅ Build sustainable customer capability through practice

---

## Development Workflow with Claude Code CLI

### Project Context

We have created **[`CLAUDE.md`](../CLAUDE.md)** at the root of the repository containing:
- Reference to `docs/domain_model_requirements.md` as the source of truth
- Project structure and coding patterns
- Key domain rules and validation logic

Claude Code automatically reads this file for project context.

### How to Use This Plan with Claude Code

This implementation plan is a **checklist** to work through iteratively. Claude Code works best when given **logical groups of related tasks** rather than trying to implement everything at once.

**Recommended Approach:**

1. **Choose a logical group of tasks** (3-5 related tasks work well)
2. **Ask Claude Code to implement that group**
3. **Review the generated code** against the domain model
4. **Run tests** to validate the implementation
5. **Move to the next group**

**What Works Well:**
- ✅ "Implement Device models (tasks 1-2): models, enums, and validation"
- ✅ "Create Device CRUD routes (task 3): create, list, get, update, delete endpoints"
- ✅ "Add Device journal functionality: journal model, routes, and auto-generation on updates"
- ✅ "Create lookup tables and routes (task 4): SampleTypes and Location"
- ✅ "Implement Device frontend: models, list view, and detail form (tasks 8-10)"

**What to Avoid:**
- ❌ "Implement all of Sub-Phase 2.1" (too broad, too many tasks)
- ❌ "Implement tasks 1-12" (mixing backend and frontend, too many concerns)

**Example Task Groupings** (feel free to choose your own):

*Backend Groups:*
- Group: Device models and validation
- Group: Device CRUD routes
- Group: Device journal functionality
- Group: Lookup tables (SampleTypes, Location)
- Group: Update Test model for multiple Devices
- Group: Update Test routes for Device array handling
- Group: Backend tests for Device

*Frontend Groups:*
- Group: Device models and data structures
- Group: Device list view with filters
- Group: Device detail/edit form
- Group: Device journal timeline view
- Group: Update Test form for multi-Device selection

**The customer should feel free to group tasks in whatever way makes sense to them.** The important principle is: **implement iteratively, validate frequently, and keep tasks logically related.**

### Using Claude Code

With the CLAUDE.md context file and this plan, you can prompt Claude Code like:

```
"Implement Device models from phase2_implementation_plan.md (tasks 1-2):
- DeviceStatus enum
- Refrigerant embedded model
- Device model with all fields
- DeviceCreate, DeviceUpdate, and DeviceQuery models

Follow the patterns from the Test model and validate against domain_model_requirements.md"
```

Claude Code will:
- Read the plan to understand what to implement
- Read the domain model for specifications
- Follow existing patterns from the codebase
- Generate consistent, validated code

**For the customer implementing Sub-Phase 2.3:**
Same approach, just reference the Environment tasks and ask Claude Code to follow the Device pattern.
