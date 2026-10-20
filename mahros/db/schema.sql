-- MAHROS platform database (PostgreSQL 16).
--
-- Idempotent: `python -m mahros.db init` runs this on every start.
--
-- Three kinds of table, and the rules differ on purpose:
--   1. REAL reference data (HHS reports, CMS capability, OSRM roads). Locked:
--      the app role has no write grant, and a trigger refuses ordinary
--      changes, including from the owner in pgAdmin. The data loader bypasses
--      it with `mahros.loading_reference`; an owner can do the same or disable
--      the trigger, so the lock prevents accidents and the audit chain
--      detects deliberate changes.
--   2. LIVE state (occupancy, hospital behaviour). The only things a person may
--      edit, and only through narrow column grants. Every change is audited.
--   3. RECORDS of what happened (transfers, agent decisions, ledger, audit).
--      Append-only for the application role.
--
-- Simulated rows are labelled as simulated. Nothing here is a real patient.

-- =========================================================================
-- 1. REAL reference data
-- =========================================================================

CREATE TABLE IF NOT EXISTS hospitals (
    hospital_id   varchar(10) PRIMARY KEY,          -- CMS Certification Number
    name          text NOT NULL,
    address       text,
    city          text,
    zip           text,
    lat           double precision NOT NULL,
    lon           double precision NOT NULL,
    tier          smallint NOT NULL CHECK (tier BETWEEN 1 AND 3),
    source        text NOT NULL
);

CREATE TABLE IF NOT EXISTS hospital_capabilities (
    hospital_id   varchar(10) NOT NULL REFERENCES hospitals,
    specialty     text NOT NULL,
    source        text NOT NULL,
    PRIMARY KEY (hospital_id, specialty)
);

CREATE TABLE IF NOT EXISTS hhs_weekly_reports (
    hospital_id     varchar(10) NOT NULL REFERENCES hospitals,
    week_start      date NOT NULL,
    inpatient_beds  double precision,
    inpatient_used  double precision,
    icu_beds        double precision,
    icu_used        double precision,
    picu_beds       double precision,
    ed_visits       double precision,
    PRIMARY KEY (hospital_id, week_start)
);

CREATE TABLE IF NOT EXISTS travel_times (
    from_id         varchar(10) NOT NULL REFERENCES hospitals,
    to_id           varchar(10) NOT NULL REFERENCES hospitals,
    road_minutes    double precision NOT NULL CHECK (road_minutes >= 0),
    source          text NOT NULL,
    PRIMARY KEY (from_id, to_id)
);

-- =========================================================================
-- 2. LIVE state
-- =========================================================================

-- One row per hospital per resource pool. Capacity and the default occupancy
-- come from real data and are locked; `occupied` is the live, editable value.
CREATE TABLE IF NOT EXISTS resources (
    resource_id          serial PRIMARY KEY,
    hospital_id          varchar(10) NOT NULL REFERENCES hospitals,
    kind                 text NOT NULL CHECK (kind IN
                           ('ward_bed','hdu_bed','icu_bed','or_slot','ventilator','cath_lab')),
    capacity             int NOT NULL CHECK (capacity >= 0),
    capacity_provenance  text NOT NULL CHECK (capacity_provenance IN ('observed','derived','assumed')),
    capacity_source      text NOT NULL,
    default_occupied     int NOT NULL,
    default_provenance   text NOT NULL CHECK (default_provenance IN ('observed','derived','assumed')),
    default_source       text NOT NULL,
    occupied             int NOT NULL,
    UNIQUE (hospital_id, kind),
    CONSTRAINT occupied_within_capacity CHECK (occupied BETWEEN 0 AND capacity),
    CONSTRAINT default_within_capacity CHECK (default_occupied BETWEEN 0 AND capacity)
);

CREATE TABLE IF NOT EXISTS hospital_settings (
    hospital_id   varchar(10) PRIMARY KEY REFERENCES hospitals,
    policy        text NOT NULL DEFAULT 'honest'
                    CHECK (policy IN ('honest','strategic','defensive'))
);

-- A "session" of the live network. Restoring the defaults starts a new one;
-- nothing from the old one is deleted.
CREATE TABLE IF NOT EXISTS live_epochs (
    epoch           serial PRIMARY KEY,
    started_at      timestamptz NOT NULL DEFAULT now(),
    started_by      text NOT NULL,
    reason          text NOT NULL,
    metro           text NOT NULL,
    reference_week  date NOT NULL
);

-- =========================================================================
-- 3. RECORDS
-- =========================================================================

CREATE TABLE IF NOT EXISTS patients (
    patient_id    uuid PRIMARY KEY,
    pseudonym     text NOT NULL,                  -- HMAC token; names are never stored
    age_band      text,
    data_origin   text NOT NULL DEFAULT 'simulated' CHECK (data_origin = 'simulated'),
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS transfer_requests (
    request_id       text PRIMARY KEY,
    epoch            int NOT NULL REFERENCES live_epochs,
    patient_id       uuid NOT NULL REFERENCES patients,
    origin_id        varchar(10) NOT NULL REFERENCES hospitals,
    resource_kind    text NOT NULL,
    specialty        text NOT NULL,
    acuity           smallint NOT NULL CHECK (acuity BETWEEN 1 AND 5),
    window_minutes   int NOT NULL,
    sim_minute       double precision NOT NULL,
    outcome          text NOT NULL CHECK (outcome IN ('awarded','failed')),
    failure_reason   text,
    rounds           smallint,
    peers_contacted  smallint,
    created_at       timestamptz NOT NULL DEFAULT now()
);

-- Every message-level decision an agent made: call for proposals, each bid
-- or refusal, each challenge, defence, overrule, and the award.
CREATE TABLE IF NOT EXISTS agent_decisions (
    decision_id   bigserial PRIMARY KEY,
    request_id    text NOT NULL REFERENCES transfer_requests,
    seq           int NOT NULL,
    agent_id      text NOT NULL,
    hospital_id   varchar(10) REFERENCES hospitals,
    action        text NOT NULL CHECK (action IN
                    ('cfp','propose','refuse','challenge','defend','attest',
                     'burden_objection','overrule','deliberation','scored',
                     'award','failed')),
    reason        text,
    payload       jsonb NOT NULL DEFAULT '{}'::jsonb,
    decided_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (request_id, seq)
);

CREATE TABLE IF NOT EXISTS challenges (
    challenge_id         bigserial PRIMARY KEY,
    request_id           text NOT NULL REFERENCES transfer_requests,
    refusal_decision_id  bigint REFERENCES agent_decisions,
    target_id            varchar(10) NOT NULL REFERENCES hospitals,
    refusal_reason       text NOT NULL,
    claim                text NOT NULL,
    evidence             jsonb NOT NULL,
    outcome              text CHECK (outcome IN ('upheld','struck_out')),
    created_at           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ledger_blocks (
    epoch          int NOT NULL REFERENCES live_epochs,
    block_index    int NOT NULL,
    block_ts       double precision NOT NULL,
    prev_hash      char(64) NOT NULL,
    merkle_root    char(64) NOT NULL,
    block_hash     char(64) NOT NULL,
    n_entries      int NOT NULL,
    sealed_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (epoch, block_index)
);

CREATE TABLE IF NOT EXISTS ledger_entries (
    epoch          int NOT NULL,
    block_index    int NOT NULL,
    position       int NOT NULL,
    payload        jsonb NOT NULL,
    PRIMARY KEY (epoch, block_index, position),
    FOREIGN KEY (epoch, block_index) REFERENCES ledger_blocks
);

CREATE TABLE IF NOT EXISTS admissions (
    admission_id          uuid PRIMARY KEY,
    patient_id            uuid NOT NULL REFERENCES patients,
    hospital_id           varchar(10) NOT NULL REFERENCES hospitals,
    resource_id           int NOT NULL REFERENCES resources,
    request_id            text REFERENCES transfer_requests,
    epoch                 int NOT NULL REFERENCES live_epochs,
    admitted_sim_minute   double precision NOT NULL,
    expected_los_minutes  double precision,
    admitted_at           timestamptz NOT NULL DEFAULT now(),
    discharged_at         timestamptz,
    discharge_reason      text
);

CREATE TABLE IF NOT EXISTS allocations (
    allocation_id         text PRIMARY KEY,         -- the engine's agreement id
    request_id            text NOT NULL UNIQUE REFERENCES transfer_requests,
    receiver_id           varchar(10) NOT NULL REFERENCES hospitals,
    admission_id          uuid REFERENCES admissions,
    decided_by            text NOT NULL,
    time_to_care_minutes  double precision,
    slack_minutes         double precision,
    contested             boolean,
    rationale             text,
    terms_hash            char(64) NOT NULL,
    ledger_epoch          int,
    ledger_block          int,
    created_at            timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (ledger_epoch, ledger_block) REFERENCES ledger_blocks
);

-- Batch experiments: one summary row per run (full detail only on request).
CREATE TABLE IF NOT EXISTS simulation_runs (
    run_id         uuid PRIMARY KEY,
    scenario       text NOT NULL,
    strategy       text NOT NULL,
    seed           int NOT NULL,
    code_version   text NOT NULL,
    params         jsonb NOT NULL,
    results        jsonb,
    created_at     timestamptz NOT NULL DEFAULT now()
);

-- The phone-tree baseline, one row per simulated call (recorded runs only).
CREATE TABLE IF NOT EXISTS phone_tree_records (
    call_id        bigserial PRIMARY KEY,
    run_id         uuid NOT NULL REFERENCES simulation_runs,
    request_ref    text NOT NULL,
    call_seq       smallint NOT NULL,
    caller_id      varchar(10) NOT NULL REFERENCES hospitals,
    callee_id      varchar(10) NOT NULL REFERENCES hospitals,
    sim_minute     double precision NOT NULL,
    answer         text NOT NULL CHECK (answer IN ('yes','no')),
    accepted       boolean NOT NULL
);

-- Model output next to what the hospital really reported (no ML involved).
CREATE TABLE IF NOT EXISTS prediction_records (
    prediction_id  bigserial PRIMARY KEY,
    run_id         uuid REFERENCES simulation_runs,
    hospital_id    varchar(10) NOT NULL REFERENCES hospitals,
    week_start     date NOT NULL,
    target         text NOT NULL,
    predicted      double precision NOT NULL,
    observed       double precision,
    method         text NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now()
);

-- Data Integrity Agent findings on the real weekly reports.
CREATE TABLE IF NOT EXISTS report_flags (
    flag_id        bigserial PRIMARY KEY,
    hospital_id    varchar(10) NOT NULL REFERENCES hospitals,
    week_start     date NOT NULL,
    rule           text NOT NULL,
    severity       text NOT NULL CHECK (severity IN ('red','amber')),
    detail         text NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (hospital_id, week_start, rule)
);

CREATE TABLE IF NOT EXISTS system_events (
    event_id       bigserial PRIMARY KEY,
    ts             timestamptz NOT NULL DEFAULT now(),
    level          text NOT NULL CHECK (level IN ('info','warning','error')),
    source         text NOT NULL,
    event_type     text NOT NULL,
    details        jsonb NOT NULL DEFAULT '{}'::jsonb
);

-- =========================================================================
-- AUDIT: a hash-chained record of every change, written by the database
-- =========================================================================

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id    bigserial PRIMARY KEY,
    ts          timestamptz NOT NULL,
    actor       text NOT NULL,
    action      text NOT NULL,
    table_name  text,
    row_pk      text,
    before      jsonb,
    after       jsonb,
    prev_hash   char(64) NOT NULL,
    row_hash    char(64) NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_log_row ON audit_log (table_name, row_pk);

-- Anchors: the head hash written down at a moment in time. A rewrite of the
-- whole chain (possible for a superuser) no longer matches an anchor.
CREATE TABLE IF NOT EXISTS audit_checkpoints (
    checkpoint_id  serial PRIMARY KEY,
    audit_id       bigint NOT NULL,
    row_hash       char(64) NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    created_by     text NOT NULL
);

-- Who is acting. The application sets `mahros.actor` in each transaction
-- ("user:reviewer", "agent:transfer-engine"); anyone else, e.g. a person in
-- pgAdmin, is recorded as their database login.
CREATE OR REPLACE FUNCTION audit_actor() RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT coalesce(nullif(current_setting('mahros.actor', true), ''),
                    'db:' || session_user)
$$;

-- The hash of one audit row. Time enters as integer microseconds so the
-- result does not depend on the session's time zone or date style.
CREATE OR REPLACE FUNCTION audit_hash(
    p_prev text, p_ts timestamptz, p_actor text, p_action text,
    p_table text, p_pk text, p_before jsonb, p_after jsonb)
RETURNS char(64) LANGUAGE sql STABLE AS $$
    SELECT encode(sha256(convert_to(
        p_prev || '|' || (extract(epoch FROM p_ts) * 1000000)::bigint::text
        || '|' || p_actor || '|' || p_action
        || '|' || coalesce(p_table, '') || '|' || coalesce(p_pk, '')
        || '|' || coalesce(p_before::text, '') || '|' || coalesce(p_after::text, ''),
        'UTF8')), 'hex')
$$;

-- Append one row to the chain. One writer at a time (advisory lock), so the
-- chain stays a single line even with concurrent transactions.
CREATE OR REPLACE FUNCTION audit_append(
    p_action text, p_table text, p_pk text, p_before jsonb, p_after jsonb)
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    v_prev  char(64);
    v_ts    timestamptz := clock_timestamp();
    v_actor text := audit_actor();
    v_id    bigint;
BEGIN
    PERFORM pg_advisory_xact_lock(7262701);
    SELECT row_hash INTO v_prev FROM audit_log ORDER BY audit_id DESC LIMIT 1;
    v_prev := coalesce(v_prev, repeat('0', 64));
    INSERT INTO audit_log (ts, actor, action, table_name, row_pk, before, after,
                           prev_hash, row_hash)
    VALUES (v_ts, v_actor, p_action, p_table, p_pk, p_before, p_after, v_prev,
            audit_hash(v_prev, v_ts, v_actor, p_action, p_table, p_pk,
                       p_before, p_after))
    RETURNING audit_id INTO v_id;
    RETURN v_id;
END $$;

-- Row trigger: before/after of every INSERT, UPDATE and DELETE. Trigger
-- arguments name the primary-key column(s).
CREATE OR REPLACE FUNCTION audit_row() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    v_old jsonb;
    v_new jsonb;
    v_pk  text := '';
    i     int;
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN v_old := to_jsonb(OLD); END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN v_new := to_jsonb(NEW); END IF;
    IF TG_OP = 'UPDATE' AND v_old = v_new THEN
        RETURN NEW;
    END IF;
    FOR i IN 0 .. TG_NARGS - 1 LOOP
        v_pk := v_pk || CASE WHEN i > 0 THEN '/' ELSE '' END
                || coalesce(coalesce(v_new, v_old) ->> TG_ARGV[i], '');
    END LOOP;
    PERFORM audit_append(TG_OP, TG_TABLE_NAME, v_pk, v_old, v_new);
    RETURN coalesce(NEW, OLD);
END $$;

-- Walk the chain and recompute every hash. Returns the first broken row.
CREATE OR REPLACE FUNCTION audit_verify()
RETURNS TABLE (ok boolean, rows_checked bigint, first_bad_id bigint, problem text)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    r     record;
    v_prev char(64) := repeat('0', 64);
    n     bigint := 0;
BEGIN
    FOR r IN SELECT * FROM audit_log ORDER BY audit_id LOOP
        n := n + 1;
        IF r.prev_hash <> v_prev THEN
            RETURN QUERY SELECT false, n, r.audit_id,
                'link broken: this row does not point at the row before it '
                || '(a row was edited, inserted or deleted)';
            RETURN;
        END IF;
        IF r.row_hash <> audit_hash(r.prev_hash, r.ts, r.actor, r.action,
                                    r.table_name, r.row_pk, r.before, r.after) THEN
            RETURN QUERY SELECT false, n, r.audit_id,
                'contents changed: this row no longer matches its own hash';
            RETURN;
        END IF;
        v_prev := r.row_hash;
    END LOOP;
    FOR r IN
        SELECT c.checkpoint_id, c.audit_id
        FROM audit_checkpoints c
        LEFT JOIN audit_log a ON a.audit_id = c.audit_id
        WHERE a.row_hash IS DISTINCT FROM c.row_hash
        ORDER BY c.checkpoint_id
    LOOP
        RETURN QUERY SELECT false, n, r.audit_id,
            'checkpoint ' || r.checkpoint_id || ' no longer matches: history up to '
            || 'that point was rewritten or cut off';
        RETURN;
    END LOOP;
    RETURN QUERY SELECT true, n, NULL::bigint, NULL::text;
END $$;

-- Write down the current head of the chain. The caller cannot choose the
-- hash it anchors, only when to anchor it.
CREATE OR REPLACE FUNCTION audit_checkpoint()
RETURNS TABLE (checkpoint_id int, audit_id bigint, row_hash char(64))
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    v_id   bigint;
    v_hash char(64);
BEGIN
    PERFORM pg_advisory_xact_lock(7262701);
    SELECT a.audit_id, a.row_hash INTO v_id, v_hash
    FROM audit_log a ORDER BY a.audit_id DESC LIMIT 1;
    IF v_id IS NULL THEN
        RAISE EXCEPTION 'MAHROS: the audit log is empty; nothing to anchor';
    END IF;
    RETURN QUERY
    INSERT INTO audit_checkpoints AS c (audit_id, row_hash, created_by)
    VALUES (v_id, v_hash, audit_actor())
    RETURNING c.checkpoint_id, c.audit_id, c.row_hash;
END $$;

-- =========================================================================
-- LOCKS on real data
-- =========================================================================

CREATE OR REPLACE FUNCTION forbid_real_data_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF current_setting('mahros.loading_reference', true) = 'on' THEN
        RETURN coalesce(NEW, OLD);
    END IF;
    RAISE EXCEPTION 'MAHROS: "%" holds real public data (HHS / CMS / OSRM) and is locked. It changes only when the data loader re-imports the source.', TG_TABLE_NAME
        USING ERRCODE = 'insufficient_privilege';
END $$;

CREATE OR REPLACE FUNCTION lock_resource_real_fields() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF current_setting('mahros.loading_reference', true) = 'on' THEN
        RETURN coalesce(NEW, OLD);
    END IF;
    IF TG_OP IN ('INSERT', 'DELETE') THEN
        RAISE EXCEPTION 'MAHROS: resource pools come from real data; they cannot be added or removed.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.capacity            IS DISTINCT FROM OLD.capacity
    OR NEW.hospital_id         IS DISTINCT FROM OLD.hospital_id
    OR NEW.kind                IS DISTINCT FROM OLD.kind
    OR NEW.capacity_provenance IS DISTINCT FROM OLD.capacity_provenance
    OR NEW.capacity_source     IS DISTINCT FROM OLD.capacity_source
    OR NEW.default_occupied    IS DISTINCT FROM OLD.default_occupied
    OR NEW.default_provenance  IS DISTINCT FROM OLD.default_provenance
    OR NEW.default_source      IS DISTINCT FROM OLD.default_source THEN
        RAISE EXCEPTION 'MAHROS: capacity and default values are real data and are locked. Only "occupied" can be changed.'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;

-- =========================================================================
-- TRIGGERS (dropped and re-created so this file stays idempotent)
-- =========================================================================

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['hospitals', 'hospital_capabilities',
                             'hhs_weekly_reports', 'travel_times'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS lock_real_data ON %I', t);
        EXECUTE format('CREATE TRIGGER lock_real_data BEFORE INSERT OR UPDATE OR DELETE '
                       'ON %I FOR EACH ROW EXECUTE FUNCTION forbid_real_data_change()', t);
    END LOOP;
END $$;

DROP TRIGGER IF EXISTS lock_real_fields ON resources;
CREATE TRIGGER lock_real_fields BEFORE INSERT OR UPDATE OR DELETE ON resources
    FOR EACH ROW EXECUTE FUNCTION lock_resource_real_fields();

DO $$
DECLARE
    spec text[];
BEGIN
    FOREACH spec SLICE 1 IN ARRAY ARRAY[
        ARRAY['resources',         'resource_id',   ''],
        ARRAY['hospital_settings', 'hospital_id',   ''],
        ARRAY['live_epochs',       'epoch',         ''],
        ARRAY['patients',          'patient_id',    ''],
        ARRAY['admissions',        'admission_id',  ''],
        ARRAY['transfer_requests', 'request_id',    ''],
        ARRAY['agent_decisions',   'decision_id',   ''],
        ARRAY['challenges',        'challenge_id',  ''],
        ARRAY['allocations',       'allocation_id', ''],
        ARRAY['ledger_blocks',     'epoch',         'block_index'],
        ARRAY['audit_checkpoints', 'checkpoint_id', '']
    ] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS audit ON %I', spec[1]);
        IF spec[3] = '' THEN
            EXECUTE format('CREATE TRIGGER audit AFTER INSERT OR UPDATE OR DELETE ON %I '
                           'FOR EACH ROW EXECUTE FUNCTION audit_row(%L)', spec[1], spec[2]);
        ELSE
            EXECUTE format('CREATE TRIGGER audit AFTER INSERT OR UPDATE OR DELETE ON %I '
                           'FOR EACH ROW EXECUTE FUNCTION audit_row(%L, %L)',
                           spec[1], spec[2], spec[3]);
        END IF;
    END LOOP;
END $$;

-- =========================================================================
-- PERMISSIONS for the application role (created by `mahros.db init`)
-- =========================================================================

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM mahros_app;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO mahros_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO mahros_app;

-- Live state: exactly the editable columns, nothing else.
GRANT UPDATE (occupied) ON resources TO mahros_app;
GRANT UPDATE (policy) ON hospital_settings TO mahros_app;
GRANT UPDATE (discharged_at, discharge_reason) ON admissions TO mahros_app;

-- Records: append-only.
GRANT INSERT ON live_epochs, patients, transfer_requests, agent_decisions,
                challenges, ledger_blocks, ledger_entries, admissions,
                allocations, simulation_runs, phone_tree_records,
                prediction_records, system_events
    TO mahros_app;

-- The audit log is written only through the database's own functions.
REVOKE ALL ON FUNCTION audit_append(text, text, text, jsonb, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION audit_append(text, text, text, jsonb, jsonb) TO mahros_app;
GRANT EXECUTE ON FUNCTION audit_verify() TO mahros_app;
REVOKE ALL ON FUNCTION audit_checkpoint() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION audit_checkpoint() TO mahros_app;
