"""Small explicit catalogue of plain full-cube vanilla materials in Java 1.21.1.
Imported exact block states remain the primary palette. No device templates.
"""
COLORS=('white','orange','magenta','light_blue','yellow','lime','pink','gray','light_gray','cyan','purple','blue','brown','green','red','black')
BASE=('stone','smooth_stone','cobblestone','stone_bricks','bricks','andesite','polished_andesite',
      'diorite','polished_diorite','granite','polished_granite','deepslate','cobbled_deepslate',
      'glass','iron_block','quartz_block','smooth_quartz','terracotta',
      'oak_planks','spruce_planks','birch_planks','jungle_planks','acacia_planks','dark_oak_planks','mangrove_planks','cherry_planks','bamboo_planks','crimson_planks','warped_planks')
CATALOG=tuple('minecraft:'+name for name in BASE)+tuple('minecraft:'+c+'_'+kind for kind in ('concrete','wool','stained_glass','terracotta') for c in COLORS)
