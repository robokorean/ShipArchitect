"""Exact X/Z change-location map and review table. Arrays bounded by 200k changes."""
import numpy as np
from PySide6.QtCore import Qt,QRectF
from PySide6.QtGui import QPainter,QColor,QImage,QPen
from PySide6.QtWidgets import QWidget,QVBoxLayout,QLabel,QSpinBox,QHBoxLayout,QPlainTextEdit,QPushButton

class ChangeMap(QWidget):
    def __init__(self):super().__init__();self.data=None;self.layer=-1;self.setMinimumHeight(250)
    def set_change(self,doc,p,report):
        self.data=(doc,p,report);self.update()
    def paintEvent(self,event):
        painter=QPainter(self);painter.fillRect(self.rect(),QColor('#111e2d'))
        if not self.data:painter.setPen(QColor('white'));painter.drawText(20,30,'새 제안의 변경 위치가 표시됩니다.');return
        doc,p,report=self.data;w,h,l=doc.schematic.size;ii=p.indices
        if not len(ii):painter.setPen(QColor('white'));painter.drawText(20,30,'변경 0개 · 실제 블록은 바뀌지 않습니다.');return
        x=ii%w;y=ii//(w*l);z=(ii//w)%l;after=p.after if p.after is not None else np.full(len(ii),p.target)
        width=min(w,700);depth=min(l,700);rgb=np.zeros((depth,width,3),np.uint8);rgb[:]=[18,30,43]
        keep=np.ones(len(ii),bool) if self.layer<0 else y==self.layer
        air={i for i,s in doc.schematic.palette.items() if s.split('[')[0] in {'minecraft:air','minecraft:cave_air','minecraft:void_air'}}
        removed=np.isin(after,list(air));added=np.isin(p.before,list(air));px=np.minimum(width-1,x*width//w);pz=np.minimum(depth-1,z*depth//l)
        for mask,color in ((added,[62,201,129]),(~added&~removed,[246,172,64]),(removed,[234,83,100])):rgb[pz[keep&mask],px[keep&mask]]=color
        image=QImage(rgb.data,width,depth,rgb.strides[0],QImage.Format.Format_RGB888).copy();scale=min((self.width()-30)/w,(self.height()-65)/l);ox=(self.width()-w*scale)/2;oz=45
        painter.drawImage(QRectF(ox,oz,w*scale,l*scale),image);b=p.bounds;painter.setPen(QPen(QColor('#f4d17a'),2));painter.drawRect(QRectF(ox+b.lo[0]*scale,oz+b.lo[2]*scale,(b.hi[0]-b.lo[0]+1)*scale,(b.hi[2]-b.lo[2]+1)*scale))
        painter.setPen(QColor('white'));painter.drawText(10,24,'+X → / +Z ↓ · 초록 추가 / 빨강 삭제 / 주황 교체 · 모든 층 투영은 겹침 표시')

class ReviewView(QWidget):
    def __init__(self,owner):
        super().__init__();self.owner=owner;layout=QVBoxLayout(self);row=QHBoxLayout();row.addWidget(QLabel('표시 Y층 (-1=전체)'));self.layer=QSpinBox();self.layer.setRange(-1,32766);self.layer.setValue(-1);row.addWidget(self.layer);self.focus=QPushButton('변경 범위로 3D 이동');self.focus.clicked.connect(self.focus_changed);row.addWidget(self.focus);layout.addLayout(row)
        self.map=ChangeMap();layout.addWidget(self.map,2);self.text=QPlainTextEdit();self.text.setReadOnly(True);layout.addWidget(self.text,1);self.layer.valueChanged.connect(self.layer_changed)
    def layer_changed(self,value):self.map.layer=value;self.map.update()
    def display(self,doc,p,report):
        self.report=report;self.map.set_change(doc,p,report);self.layer.setMaximum(doc.schematic.size[1]-1)
        parts=[f"실제 최종 변경 {report['changed']:,}개 · 추가 {report['added']:,} / 삭제 {report['removed']:,} / 교체 {report['replaced']:,}",f"선택 범위 {p.bounds.lo} → {p.bounds.hi}",f"실제 변경 범위 {report['bbox']}",*report['warnings'],'재료별 실제 최종 수량:']
        parts += [f'{k}: {n:,}' for k,n in report['materials'].items()];parts+=['변경 전 → 후:']+[f'{k}: {n:,}' for k,n in report['transitions'].items()]
        parts+=['Y층별 변경: '+str(report['layers']),'청크 위치(32³, XYZ): '+str(report['chunks'])]
        self.text.setPlainText('\n'.join(parts))
    def focus_changed(self):
        if not getattr(self,'report',None) or not self.report['bbox']:return
        # Fit camera to changed bounds without changing the approved selection token.
        from .model import Bounds
        self.owner.view.focus_bounds=Bounds(tuple(self.report['bbox']['lo']),tuple(self.report['bbox']['hi']))
        old=self.owner.doc.selection;self.owner.doc.selection=self.owner.view.focus_bounds
        try:self.owner.view.focus_selection()
        finally:self.owner.doc.selection=old
        self.owner.tabs.setCurrentWidget(self.owner.view)
