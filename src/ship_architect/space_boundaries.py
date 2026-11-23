"""Interval-level boundary comparison without converting air contact into a route."""
from collections import defaultdict


def boundary_relation(first, second):
    left, right = defaultdict(list), defaultdict(list)
    for record, rows in ((first,left),(second,right)):
        for y,z,a,b in record.get('runs',record.get('volume_runs',[])):
            rows[y,z].append((a,b))

    def overlap(spans, others):
        return sum(max(0,min(b,d)-max(a,c)+1) for a,b in spans for c,d in others)

    intersection = sum(overlap(spans,right.get(row,())) for row,spans in left.items())
    faces = 0
    samples = []
    for (y,z), spans in left.items():
        for dy,dz in ((-1,0),(1,0),(0,-1),(0,1)):
            count = overlap(spans,right.get((y+dy,z+dz),()))
            faces += count
            if count and len(samples)<32:
                samples.append(dict(row=[y,z],neighbor=[y+dy,z+dz],unit_faces=count))
        neighbors = right.get((y,z),())
        for a,b in spans:
            for c,d in neighbors:
                if b+1==c or d+1==a:
                    faces += 1
                    if len(samples)<32:
                        samples.append(dict(row=[y,z],x_boundary=max(a,c),unit_faces=1))
    return dict(first=first['id'],second=second['id'],overlap_cells=intersection,
                shared_air_faces=faces,contact_sample=samples,
                geometry='overlap' if intersection else 'air_face_contact' if faces else 'separate',
                traversability='unverified',
                reason='실제 공기 셀의 겹침·6면 접촉을 비교했습니다. 통행은 바닥·머리 공간·문·계단 분석이 필요합니다.')
