# 7. Continuous Compliance & OSCAL Assessment via Defense Unicorns Lula

Date: 2026-10-13

## Status
Accepted

## Context
Tactical systems deployed into Department of Defense and federal environments must achieve and maintain an Authorization to Operate (ATO) or continuous ATO (cATO). 

Traditional compliance verification relies on static, manual spreadsheets and checklists that drift out of sync with actual software baselines. Defense Unicorns champions automated, declarative compliance using **Lula**—an open-source tool that maps live system configurations, Kubernetes resources, and container states against **NIST SP 800-53** controls defined in machine-readable **OSCAL** (Open Security Controls Assessment Language).

## Decision
1. Introduce automated compliance-as-code into the project using Defense Unicorns **Lula** (`lula.yaml`).
2. Map gateway architectural and operational controls to NIST SP 800-53 Rev 5 baselines:
   - **SC-8 (Transmission Confidentiality & Integrity):** Verified via TLS/mTLS egress and encrypted radio transport validation.
   - **AC-3 / AC-6 (Access Enforcement & Least Privilege):** Verified via rootless container configuration (`UID 10001`, drop all Linux capabilities).
   - **SI-4 / SI-7 (Information System Monitoring & Software Integrity):** Verified via deterministic cryptographic key derivation and immutable container packaging.
3. Provide a make target (`make compliance-test`) executing `lula validate` to produce audit-ready compliance assessments directly from the repository.

## Consequences
- Elevates the project from an isolated coding experiment to a compliance-audited, cATO-ready federal defense capability.
- Directly demonstrates active expertise with Defense Unicorns' open source compliance ecosystem (Lula/OSCAL).
