# v0.9.3.11

- 공간 표시를 16셀 청크로 분리하고 이웃 청크를 확인해 경계 내부 면을 제거합니다. 화면 밖 면을 먼저 거른 뒤 예산을 계산합니다.
- 대량 표시를 정밀·간략·요약으로 구분하고 활성 공간은 별도 예산으로 점진 표시합니다. 공간 범위 판정과 설계는 정확한 원본 셀을 사용합니다.
- 그래픽 품질과 예산 설정, 성능 진단, 탐색기 요약 이름과 즐겨찾기, 읽기 쉬운 속성 및 일괄 편집, 다섯 가지 작업 표시 프리셋을 추가합니다.
- PR #1의 후보 삭제·제외 후 공간 관계 정리를 포함합니다. 기존 인식 엔진은 그대로 사용할 수 있습니다.

# v0.9.3.10

- 통합 정밀 인식에서 실제 바닥·천장 사이 대형 개방 후보를 검토 구획으로 분리합니다. 인접 조각과 잔여 공기를 정확한 셀과 함께 보존합니다.
- 프로젝트 탐색기에 확정 공간, 미확정 후보, 설비, 통로·계단·승강기·출입구 및 이동 경로를 모았습니다.
- 탐색기와 도면·3D에서 선택을 공유합니다. 선택은 정확한 셀이나 실제 경로로 강조하며 F로 시점을 이동합니다.
- 개별·그룹·전체 삭제, 지속 제외, 해제, 실행 취소와 다시 실행을 지원합니다.

# v0.9.3.9

- Add a fused detection engine with exact Legacy-only, shared and Hybrid-only voxel provenance.
- Preserve distinct spaces between local observed deck and roof pairs and keep residual partial-deck openings reviewable.
- Retain thin and uncertain discoveries with independent inspection filters, occupancy and usage evidence.
- Keep prior engines selectable and the Windows CMD/Pythonw launch paths.

# v0.9.3.8

- Keep physical deck ownership constraints during Hybrid seed expansion and test large unresolved candidates at structure-supported aperture boundaries.
- Retain bounded probable unfinished interiors for review and preserve validated Legacy boundaries, exact voxel geometry and separate physical-air connectivity.
- Share the persisted analysis engine between both panels. Retain the prior Hybrid for comparison, along with Structural and Legacy.
- Replace the VBS entry with a regular Windows command and an optional Python windowed entry. Keep private dependency installation and startup logging.

# v0.9.3.7

- Structural recognition derives candidate air and floor-anchored partitions from local geometry before room labelling.
- Hybrid adds bounded recovery at open edges and preserves strongly supported, validated Legacy boundaries where the structural evidence independently agrees.
- Keep Legacy available and selected by default while real-world room accuracy is still being evaluated. Add same-input comparison and engine provenance.
- Apply user-marked analysis planes only to the candidate graph for the structural engines. Export structural metrics and retain project, undo, block and NBT compatibility.

# v0.9.3.6

- Add independently selectable structure evidence in the 2D and 3D editors, including hull surfaces, deck and support geometry, equipment, observed air interfaces, virtual boundaries and both air masks.
- Record directional opening evidence, paired deck and aperture evidence, structural classification and equipment adjacency while retaining exact legacy candidate geometry.
- Add analysis-only review holds and manually marked virtual planes with Undo/Redo, project and snapshot persistence, and diagnostic detail export.
- Verify the runtime ZIP separately from historical project data and development evidence.

# v0.9.3.5

- Show all regular analysis candidates independently of selection.
- Open raw schematics without restoring stored space or visibility state.
- Save and reload named recognition snapshots and guard mismatched block signatures.
- Add reversible metadata-only bulk deletion and consolidate analysis actions.
- Retain the ceiling exposure fix and PowerShell-free launchers from 0.9.3.4.1.

# v0.9.3.4

- Explicit clean and protected recognition sessions, saved snapshot comparison and exact-cell difference overlays.
- Candidate provenance and block state signatures; confirmed spaces change through explicit registration.
- Broad unresolved volumes grouped at paired physical deck faces without changing any air or blocks.
- Windows console and GUI launch entry points with startup logs and work-stage splash.

# v0.9.3.3

- Use broad boundary-connected soil as environmental terrain during exterior analysis.
- Repair wide artificial seams supported by continuous floors and roofs; preserve narrow and vertical passages.
- Expose physical contacts, exterior opening samples, adjacent candidates and repair decisions.
- Add review overlays, persistent partition rejection and exact recognition JSON export.
- Preserve original block and NBT data, existing connectivity mode and manually edited spaces.
- Large exterior-connected multi-deck components and ambiguous opening classifications still require review.

# v0.9.3.2

- Separate view-only visibility/opacity/mesh controls from space geometry.
- Add confirmed scene deletion, multi-selection, undo, exclusion and candidate cleanup.
- Classify broad exterior/merged hypotheses separately and prevent room registration.
- Preserve exact voxel boundary mesh and highlight true corner edges.
- Cache complex overlays asynchronously and retain the complete ship mesh during rotation.
- Add conservative addon equipment ID classification and diagnostic presentation state.
- Create runtime-only packaging and independent extracted-folder verification.
- Keep private inputs and derived diagnostic/screenshot evidence outside Git and runtime archives.
