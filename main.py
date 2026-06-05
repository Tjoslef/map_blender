import math
import os
from pathlib import Path

import bpy

from pipeline import from_file as test_run
from pipeline import run as run_pipeline

BASE_DIR = Path(__file__).resolve().parent
GPX_PATH = BASE_DIR / "lysa.gpx"
IMAGE_PATH = BASE_DIR / "map_tiles.png"
BLEND_PATH = BASE_DIR / "gpx_map.blend"


def main():

    result = test_run("output.json", "vysledna_mapa.png")
    result.map_image.save(IMAGE_PATH)
    res = result.grid_size
    SCALE = 0.1  # meters → more manageable size
    vertices = [
        (pt["x"] * SCALE, pt["y"] * SCALE, pt["z"] * SCALE) for pt in result.terrain
    ]
    x_min = result.bounds[0]
    x_max = result.bounds[1]
    y_min = result.bounds[2]
    y_max = result.bounds[3]
    zs = [v[2] for v in vertices]
    cx, cy, cz = (x_min + x_max) / 2, (y_min + y_max) / 2, (min(zs) + max(zs)) / 2
    diag = math.hypot(x_max - x_min, y_max - y_min)
    cam_data = bpy.data.cameras.new("Scene_Camera")
    cam_obj = bpy.data.objects.new("Scene_Camera", cam_data)
    bpy.context.collection.objects.link(cam_obj)
    cam_obj.location = (cx, cy - diag * 1.2, cz + diag * 0.8)
    quads = []
    for r in range(res - 1):
        for c in range(res - 1):
            v0 = r * res + c
            v1 = v0 + 1
            v2 = (r + 1) * res + c + 1
            v3 = (r + 1) * res + c
            quads.append((v0, v1, v2, v3))
    mesh = bpy.data.meshes.new("Terrain_Mesh")
    mesh.from_pydata(vertices, [], quads)
    mesh.update()

    terrain_obj = bpy.data.objects.new("3D_Terrain", mesh)
    bpy.context.collection.objects.link(terrain_obj)
    for polygon in mesh.polygons:
        polygon.use_smooth = True
    mesh.uv_layers.new(name="Terrain_UV")
    uv_layer = mesh.uv_layers.active.data
    for polygon in mesh.polygons:
        for loop_index in polygon.loop_indices:
            vertex_index = mesh.loops[loop_index].vertex_index

            # Zjistíme, na jakém řádku a sloupci bod leží
            r = vertex_index // res
            c = vertex_index % res

            # Přepočet na hodnoty od 0.0 do 1.0 (X_uv, Y_uv)
            uv_x = c / (res - 1)
            uv_y = r / (res - 1)

            uv_layer[loop_index].uv = (uv_x, uv_y)

    # --- KROK 3: MATERIÁL A TEXTURA ---
    # Vytvoříme nový materiál, který bude držet náš obrázek
    mat = bpy.data.materials.new(name="Terrain_Material")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    # Najdeme hlavní uzly materiálu
    principled = nodes.get("Principled BSDF")
    output = nodes.get("Material Output")

    # Vytvoříme uzel pro obrázek (Image Texture Node)
    texture_node = nodes.new(type="ShaderNodeTexImage")

    # Načteme náš poslepovaný obrázek z disku do Blenderu
    if os.path.exists(IMAGE_PATH):
        blender_image = bpy.data.images.load(str(IMAGE_PATH))
        texture_node.image = blender_image
    else:
        print(f"POZOR: Obrázek na cestě {IMAGE_PATH} nebyl nalezen!")

    # Propojíme texturu s barvou materiálu (Color -> Base Color)
    links.new(texture_node.outputs["Color"], principled.inputs["Base Color"])

    # Přiřadíme hotový materiál našemu terénu
    if len(terrain_obj.data.materials) == 0:
        terrain_obj.data.materials.append(mat)
    else:
        terrain_obj.data.materials[0] = mat

    bpy.ops.wm.save_as_mainfile(filepath=str(BLEND_PATH))
    print(f"Saved Blender scene to {BLEND_PATH}")
    print("3D Terén s texturou byl úspěšně vygenerován!")


if __name__ == "__main__":
    main()
