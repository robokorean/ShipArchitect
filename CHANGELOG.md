# v0.9.4.1

- 선택 후보 확정·등록의 검증·중복 검사·메타데이터/Undo 준비를 백그라운드로 이동하고 원자적 적용·취소·오래된 결과 차단을 추가합니다.
- 후보 ID 집합/색인을 한 번 구성하고 중복 복사·팔레트 검사를 줄입니다. 현재 문서와 분리한 이력 스냅샷은 불변 부분을 공유합니다. 판단·원본 복셀·NBT·기존 공간과 시설 관계는 유지합니다.
- 등록 단계별 시간과 최종 화면 갱신 비용을 진단에 기록합니다.
- 대형 공간/시설의 현재 선택 교집합 AI 설계와 bounded context, 명령 재실행의 부분 범위를 제공합니다.
- 사용자가 제공한 ‘억지로 건축하지 말고’ 문장을 일반 한국어로 해석하며 미확인 실제 재료는 계속 오류 처리합니다.

# v0.9.4

- 기본 화면을 열기 → 공간 분석 → 선택·수정 → AI 내부 설계 → 변경 검토 → 저장으로 정리하고, 기존 리본·분석·수동 편집은 고급 작업공간에 유지합니다.
- 사용자 확인 시설의 여러 공간을 정확한 셀 합집합으로 설계합니다. 원래 공간 ID·확정 정보는 그대로 두고 시설 밖·빈틈의 배치를 차단합니다.
- 시설·설비·미확정 경로 근거를 AI에 전달하고 Python이 좌표·재료·보호·기존 이동 경로를 검증합니다. 실제 장치 동작과 게임 충돌은 추정하지 않습니다.
- JSON/재료 수정과 대화 재실행에도 시설 범위·내부 설계 모드를 유지합니다. 선택 변경 시 이전 승인을 해제하며 승인·저장·재열기 후 Undo/Redo를 지원합니다.
- 기본 탐색기에서는 보조 열을 접어 공간·시설 이름을 읽기 쉽게 하고 고급 화면의 사용자 열 배치를 보존합니다.
- 한국어 부정 명령을 재료 이름으로 오해하거나 ‘통로’를 ‘통’ 블록으로 해석하던 문제를 수정합니다.
- 실함 재분석, 129개 확정 공간·블록·NBT 보존과 실제 Ollama/Qwen3 소규모 시설 설계 검증을 추가합니다. 사용자 PC 실사용 및 대규모 시설 설계 품질은 별도 확인 대상입니다.

# v0.9.3.13

- 사용자 승인 시설 그룹으로 확정 공간과 후보를 함께 관리한다. 경계·ID를 보존하고 시설 해제, 소속 추가, 삭제·분할 후 소속 갱신 및 Undo/Redo를 제공한다.
- 두 공간의 정확한 겹침·6면 공기 접촉을 비교하고 다중 강조색을 구분한다. 시설의 논리적 관계와 정적 통행 결과를 분리한다.
- CC 컴퓨터·주변장치, Create 기계·연결 부품, 추진·전력·승강기 후보를 구분하고 확정·후보 공간과의 관계 및 설비 인접 근거를 기록한다.
- 내부 후보도 공간 확정 없이 정적 경로를 검사한다. 게임 충돌·승강기 동작은 미검증 상태로 유지한다.
- 화면 변환별 QPicture 캐시, 외곽선 일괄 그리기와 정확한 2D 마스크 캐시를 추가한다. 메시 캐시·LOD·부분 갱신과 기존 인식 결과를 유지한다.
- 진단 ZIP에 시설 그룹과 표시 캐시 측정 정보를 포함한다. 최신 사용 안내를 정리한다.

# v0.9.3.12

- 공간 분석 버튼 하나로 통합 정밀 분석, 중요도 분류, 주요 공간 표시를 실행합니다.
- 중요 공간의 별도 표시 예산, 부속 조각 그룹, 고급 후보 필터 및 선택의 표시 상태를 추가합니다.
- 정확한 빈 복셀 경계로 안전한 미리보기 합치기·분할과 소속 편집을 제공합니다.
- 원본 블록, NBT, 후보 지문과 도메인, 기존 확정 공간을 보존합니다.

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
