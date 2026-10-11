-- Named, parameterised metric queries. analytics.py loads this file; the NL feature can only
-- choose from these names. Every query accepts :branch and :company (NULL = no filter).
--
-- Counting unit: a student x drive pair ("opportunity"). The funnel only counts ELIGIBLE pairs:
--   eligible -> registered -> appeared -> selected   (each stage is a subset of the previous one)
-- Registrations by ineligible students are reported separately (policy_exceptions).

-- name: eligibility
-- description: Number of eligible students per drive (CGPA, backlog and branch rules)
SELECT f.drive_id, f.company_name, d.role,
       SUM(f.is_eligible)                                   AS eligible_students,
       COUNT(*)                                             AS total_students,
       ROUND(100.0 * SUM(f.is_eligible) / COUNT(*), 2)      AS eligible_pct
FROM v_funnel f
JOIN dim_drive d ON d.drive_id = f.drive_id
WHERE (:branch IS NULL OR f.branch_code = :branch)
  AND (:company IS NULL OR f.company_name = :company)
GROUP BY f.drive_id, f.company_name, d.role
ORDER BY f.drive_id;

-- name: participation
-- description: Registration/participation: eligible students who registered, and the participation rate
SELECT COALESCE(SUM(is_eligible), 0)                                   AS eligible,
       COALESCE(SUM(is_eligible * is_registered), 0)                   AS registered,
       ROUND(100.0 * SUM(is_eligible * is_registered) / NULLIF(SUM(is_eligible), 0), 2) AS participation_rate_pct
FROM v_funnel
WHERE (:branch IS NULL OR branch_code = :branch)
  AND (:company IS NULL OR company_name = :company);

-- name: appearance
-- description: Appearance: registered students who actually appeared, and the appearance rate
SELECT COALESCE(SUM(is_eligible * is_registered), 0)                   AS registered,
       COALESCE(SUM(is_eligible * is_registered * is_appeared), 0)     AS appeared,
       ROUND(100.0 * SUM(is_eligible * is_registered * is_appeared)
             / NULLIF(SUM(is_eligible * is_registered), 0), 2)         AS appearance_rate_pct
FROM v_funnel
WHERE (:branch IS NULL OR branch_code = :branch)
  AND (:company IS NULL OR company_name = :company);

-- name: selection
-- description: Selection: students who appeared and were selected, and the selection rate
SELECT COALESCE(SUM(is_eligible * is_registered * is_appeared), 0)                 AS appeared,
       COALESCE(SUM(is_eligible * is_registered * is_appeared * is_selected), 0)   AS selected,
       ROUND(100.0 * SUM(is_eligible * is_registered * is_appeared * is_selected)
             / NULLIF(SUM(is_eligible * is_registered * is_appeared), 0), 2)       AS selection_rate_pct
FROM v_funnel
WHERE (:branch IS NULL OR branch_code = :branch)
  AND (:company IS NULL OR company_name = :company);

-- name: funnel
-- description: Selection funnel: eligible -> registered -> appeared -> selected, with stage conversion
WITH t AS (
    SELECT COALESCE(SUM(is_eligible), 0)                                              AS eligible,
           COALESCE(SUM(is_eligible * is_registered), 0)                              AS registered,
           COALESCE(SUM(is_eligible * is_registered * is_appeared), 0)                AS appeared,
           COALESCE(SUM(is_eligible * is_registered * is_appeared * is_selected), 0)  AS selected
    FROM v_funnel
    WHERE (:branch IS NULL OR branch_code = :branch)
      AND (:company IS NULL OR company_name = :company)
)
SELECT 1 AS stage_no, 'Eligible' AS stage, eligible AS students,
       100.0 AS pct_of_eligible, NULL AS pct_of_previous_stage FROM t
UNION ALL
SELECT 2, 'Registered', registered, ROUND(100.0 * registered / NULLIF(eligible, 0), 2),
       ROUND(100.0 * registered / NULLIF(eligible, 0), 2) FROM t
UNION ALL
SELECT 3, 'Appeared', appeared, ROUND(100.0 * appeared / NULLIF(eligible, 0), 2),
       ROUND(100.0 * appeared / NULLIF(registered, 0), 2) FROM t
UNION ALL
SELECT 4, 'Selected', selected, ROUND(100.0 * selected / NULLIF(eligible, 0), 2),
       ROUND(100.0 * selected / NULLIF(appeared, 0), 2) FROM t
ORDER BY stage_no;

-- name: branch_metrics
-- description: Branch-wise eligibility, participation, appearance, selection and rates
SELECT b.branch_code,
       SUM(f.is_eligible)                                              AS eligible,
       SUM(f.is_eligible * f.is_registered)                            AS registered,
       SUM(f.is_eligible * f.is_registered * f.is_appeared)            AS appeared,
       SUM(f.is_eligible * f.is_registered * f.is_appeared * f.is_selected) AS selected,
       ROUND(100.0 * SUM(f.is_eligible * f.is_registered) / NULLIF(SUM(f.is_eligible), 0), 2) AS participation_rate_pct,
       ROUND(100.0 * SUM(f.is_eligible * f.is_registered * f.is_appeared * f.is_selected)
             / NULLIF(SUM(f.is_eligible * f.is_registered * f.is_appeared), 0), 2)            AS selection_rate_pct
FROM dim_branch b
LEFT JOIN v_funnel f ON f.branch_code = b.branch_code
     AND (:company IS NULL OR f.company_name = :company)
WHERE (:branch IS NULL OR b.branch_code = :branch)
GROUP BY b.branch_code
ORDER BY b.branch_code;

-- name: company_metrics
-- description: Company-wise eligibility, participation, appearance, selection and rates
SELECT c.company_name,
       COALESCE(SUM(f.is_eligible), 0)                                 AS eligible,
       COALESCE(SUM(f.is_eligible * f.is_registered), 0)               AS registered,
       COALESCE(SUM(f.is_eligible * f.is_registered * f.is_appeared), 0) AS appeared,
       COALESCE(SUM(f.is_eligible * f.is_registered * f.is_appeared * f.is_selected), 0) AS selected,
       ROUND(100.0 * SUM(f.is_eligible * f.is_registered) / NULLIF(SUM(f.is_eligible), 0), 2) AS participation_rate_pct,
       ROUND(100.0 * SUM(f.is_eligible * f.is_registered * f.is_appeared * f.is_selected)
             / NULLIF(SUM(f.is_eligible * f.is_registered * f.is_appeared), 0), 2)            AS selection_rate_pct
FROM dim_company c
LEFT JOIN v_funnel f ON f.company_id = c.company_id
     AND (:branch IS NULL OR f.branch_code = :branch)
WHERE (:company IS NULL OR c.company_name = :company)
GROUP BY c.company_name
ORDER BY c.company_name;

-- name: policy_exceptions
-- description: Registrations by students who did NOT meet the eligibility rules (data/policy exceptions)
SELECT COALESCE(SUM(is_registered), 0)                                  AS total_registrations,
       COALESCE(SUM((1 - is_eligible) * is_registered), 0)             AS ineligible_registrations,
       ROUND(100.0 * SUM((1 - is_eligible) * is_registered) / NULLIF(SUM(is_registered), 0), 2) AS ineligible_pct
FROM v_funnel
WHERE (:branch IS NULL OR branch_code = :branch)
  AND (:company IS NULL OR company_name = :company);
