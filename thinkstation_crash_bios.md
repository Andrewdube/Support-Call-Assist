---
title: ThinkStation - Random crashes / BSOD after BIOS update
models: ThinkStation P360, P3, P5, P620, P7, P8
symptoms: crash, reboot, BSOD, blue screen, freeze, WHEA, after BIOS update, after hardware replacement
parts: none until BIOS ruled out; Memory DIMM; GPU; System board
---
SAMPLE ARTICLE - replace with your organization's approved content.

Known pattern: crashes that persist after replacing system board, GPU and memory are often firmware related.
1. Record the BIOS version and the date crashes began. Check whether a BIOS was released/installed shortly before.
2. Collect Event Viewer (System log: WHEA-Logger, Kernel-Power 41) and minidumps (C:\Windows\Minidump).
3. Run Lenovo Diagnostics (UEFI) full memory and CPU tests.
4. Update chipset, GPU and storage drivers via Lenovo System Update / Commercial Vantage.
5. Test with BIOS rollback to the previous stable version (allow rollback in BIOS security settings). If crashes stop on the older BIOS, do NOT replace further hardware - escalate to L2/engineering with BIOS versions, dumps and case history; check for other open cases with the same MT and BIOS (possible BIOS defect).
6. Only replace hardware if diagnostics fail on a specific component.
