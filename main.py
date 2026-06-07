import math
from pathlib import Path

import bpy
from mathutils import Vector

from pipeline import from_file, run

BASE_DIR = Path(__file__).resolve().parent
GPX_PATH = BASE_DIR / "lysa.gpx"
IMAGE_PATH = BASE_DIR / "map_tiles.png"
BLEND_PATH = BASE_DIR / "gpx_map.blend"
OUTPUTJSON = BASE_DIR / "output.json"
SCALE = 0.1  # meters → more manageable size


def latlon_to_xy(lat, lon, origin_lat, origin_lon):
    x = (lon - origin_lon) * math.cos(math.radians(origin_lat)) * 111320
    y = (lat - origin_lat) * 111320
    return x, y


def create_base_tree():

    bpy.ops.mesh.primitive_cylinder_add(radius=0.2, depth=3, location=(0, 0, 1.5))
    trunk = bpy.context.active_object
    trunk.name = "BaseTree_Trunk"

    bpy.ops.mesh.primitive_ico_sphere_add(radius=2, subdivisions=2, location=(0, 0, 4))
    canopy = bpy.context.active_object
    canopy.name = "BaseTree_Canopy"

    trunk.select_set(True)
    bpy.context.view_layer.objects.active = trunk
    bpy.ops.object.join()

    tree = bpy.context.active_object
    tree.name = "BaseTree"

    # Move to excluded collection so it's not visible directly
    tree_collection = bpy.data.collections.new("TreeAssets")
    bpy.context.scene.collection.children.link(tree_collection)
    tree_collection.objects.link(tree)
    bpy.context.scene.collection.objects.unlink(tree)
    tree_collection.hide_viewport = True  # hidden but usable by GeoNodes

    return tree


def setup_geometry_nodes_scatter(floor_obj, tree_obj):
    mod = floor_obj.modifiers.new("ForestScatter", "NODES")
    node_group = bpy.data.node_groups.new("ForestScatter", "GeometryNodeTree")
    mod.node_group = node_group

    nodes = node_group.nodes
    links = node_group.links

    # Interface
    node_group.interface.new_socket(
        "Geometry", in_out="INPUT", socket_type="NodeSocketGeometry"
    )
    node_group.interface.new_socket(
        "Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry"
    )

    input_node = nodes.new("NodeGroupInput")
    input_node.location = (-600, 0)
    output_node = nodes.new("NodeGroupOutput")
    output_node.location = (600, 0)

    # Distribute points on faces
    distribute = nodes.new("GeometryNodeDistributePointsOnFaces")
    distribute.location = (-300, 0)
    distribute.inputs["Density"].default_value = 0.02  # trees/m²

    # Instance on points
    instance = nodes.new("GeometryNodeInstanceOnPoints")
    instance.location = (0, 0)

    # Object info (the tree object)
    obj_info = nodes.new("GeometryNodeObjectInfo")
    obj_info.location = (-300, -200)
    obj_info.inputs["Object"].default_value = tree_obj

    # Random rotation
    rand_rot = nodes.new("FunctionNodeRandomValue")
    rand_rot.location = (-300, 200)
    rand_rot.data_type = "FLOAT"
    rand_rot.inputs[2].default_value = 0.0
    rand_rot.inputs[3].default_value = math.tau

    # Join geometry
    join = nodes.new("GeometryNodeJoinGeometry")
    join.location = (300, 0)

    # Wire it up
    links.new(input_node.outputs["Geometry"], distribute.inputs["Mesh"])
    links.new(distribute.outputs["Points"], instance.inputs["Points"])
    links.new(obj_info.outputs["Geometry"], instance.inputs["Instance"])
    links.new(rand_rot.outputs["Value"], instance.inputs["Rotation"])
    links.new(instance.outputs["Instances"], join.inputs["Geometry"])
    links.new(input_node.outputs["Geometry"], join.inputs["Geometry"])
    links.new(join.outputs["Geometry"], output_node.inputs["Geometry"])


def create_forest_floor(coords, name="ForestFloor"):
    """Extrude a flat polygon from the OSM outline"""
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)

    bm = bmesh.new()

    verts = [bm.verts.new((x, y, 0.0)) for x, y in coords[:-1]]
    bm.faces.new(verts)

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(mesh)
    bm.free()

    # Green forest floor material
    mat = bpy.data.materials.new(name + "_mat")
    mat.use_nodes = True
    mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (
        0.08,
        0.25,
        0.05,
        1.0,
    )
    obj.data.materials.append(mat)

    return obj


def light():
    for obj in bpy.data.objects:
        if obj.type == "LIGHT":
            bpy.data.objects.remove(obj)
    sun = bpy.data.lights.new("Sun", "SUN")
    sun.energy = 4
    sun.angle = math.radians(5)
    sun_obj = bpy.data.objects.new("Sun", sun)
    bpy.context.collection.objects.link(sun_obj)
    sun_obj.rotation_euler = (math.radians(45), 0, math.radians(30))
    world = bpy.context.scene.world
    world.use_nodes = True
    bg = world.node_tree.nodes.new("ShaderNodeBackground")
    bg.inputs["Color"].default_value = (0.15, 0.15, 0.2, 1)  # subtle blue-gray
    bg.inputs["Strength"].default_value = 0.4
    world.node_tree.links.new(
        bg.outputs["Background"],
        world.node_tree.nodes["World Output"].inputs["Surface"],
    )


def camera(vertices):
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    zs = [v[2] for v in vertices]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    z_min, z_max = min(zs), max(zs)
    cx, cy, cz = (x_min + x_max) / 2, (y_min + y_max) / 2, (z_min + z_max) / 2
    diag = math.hypot(x_max - x_min, y_max - y_min)
    cam_data = bpy.data.cameras.new("Scene_Camera")
    cam_obj = bpy.data.objects.new("Scene_Camera", cam_data)
    bpy.context.collection.objects.link(cam_obj)
    cam_obj.location = (cx, cy - diag * 1.2, cz + diag * 0.8)

    center = Vector((cx, cy, cz))
    loc = Vector(cam_obj.location)
    direction = center - loc
    cam_obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

    bpy.context.scene.camera = cam_obj


def course(result):
    points = [
        (pt["x"] * SCALE, pt["y"] * SCALE, pt["z"] * SCALE) for pt in result.route_xyz
    ]

    curve_data = bpy.data.curves.new("Route_Curve", "CURVE")
    curve_data.dimensions = "3D"
    curve_data.bevel_depth = 0.3

    spline = curve_data.splines.new("POLY")
    spline.points.add(len(points) - 1)
    for i, pt in enumerate(points):
        spline.points[i].co = (pt[0], pt[1], pt[2], 1)

    curve_obj = bpy.data.objects.new("Route_Object", curve_data)
    bpy.context.collection.objects.link(curve_obj)

    red_mat = bpy.data.materials.new(name="Red_Course")
    red_mat.use_nodes = True
    principled = red_mat.node_tree.nodes.get("Principled BSDF")
    principled.inputs["Base Color"].default_value = (1.0, 0.0, 0.0, 1.0)
    principled.inputs["Roughness"].default_value = 0.4
    curve_obj.data.materials.append(red_mat)


def main():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)

    result = run(GPX_PATH, 16)
    result.map_image.save(IMAGE_PATH)
    res = result.grid_size
    vertices = [
        (pt["x"] * SCALE, pt["y"] * SCALE, pt["z"] * SCALE) for pt in result.terrain
    ]
    quads = []
    for r in range(res - 1):
        for c in range(res - 1):
            v0 = r * res + c
            v1 = v0 + 1
            v2 = (r + 1) * res + c + 1
            v3 = (r + 1) * res + c
            quads.append((v0, v3, v2, v1))

    course(result)
    mesh = bpy.data.meshes.new("Terrain_Mesh")
    mesh.from_pydata(vertices, [], quads)
    mesh.update()
    light()
    camera(vertices)
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
            uv_y = 1.0 - r / (res - 1)

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
    if IMAGE_PATH.exists():
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
    parser_features(result)

    bpy.ops.wm.save_as_mainfile(filepath=str(BLEND_PATH))
    print(f"Saved Blender scene to {BLEND_PATH}")
    print("3D Terén s texturou byl úspěšně vygenerován!")


def draw_buildings(buildings):
    for feature in buildings:
        pts = feature["geometry"]
        if len(pts) < 3:
            continue
        coords = [(pt["x"] * SCALE, pt["y"] * SCALE, 0) for pt in pts]
        mesh = bpy.data.meshes.new(name=f"Building_{feature['id']}")
        mesh.from_pydata(coords, [], [list(range(len(coords)))])
        mesh.update()
        obj = bpy.data.objects.new(f"Building_{feature['id']}", mesh)
        bpy.context.collection.objects.link(obj)


def draw_forests(forests):
    base_tree = create_base_tree()
    for feature in forests:
        pts = feature["geometry"]
        if len(pts) < 3:
            continue
        coords = [(pt["x"] * SCALE, pt["y"] * SCALE, 0.05) for pt in pts]
        floor = create_forest_floor(coords, name=f"Forest_{feature}")
        setup_geometry_nodes_scatter(floor, base_tree)


def draw_water(water_features):
    for feature in water_features:
        pts = feature["geometry"]
        if len(pts) < 3:
            continue
        coords = [(pt["x"] * SCALE, pt["y"] * SCALE, -0.05) for pt in pts]
        mesh = bpy.data.meshes.new(name=f"Water_{feature['id']}")
        mesh.from_pydata(coords, [], [list(range(len(coords)))])
        mesh.update()
        obj = bpy.data.objects.new(f"Water_{feature['id']}", mesh)
        bpy.context.collection.objects.link(obj)
        mat = bpy.data.materials.new(name=f"Water_Mat_{feature['id']}")
        mat.use_nodes = True
        principled = mat.node_tree.nodes.get("Principled BSDF")
        principled.inputs["Base Color"].default_value = (0.1, 0.3, 0.8, 1.0)
        obj.data.materials.append(mat)


def draw_roads(roads):
    for feature in roads:
        pts = feature["geometry"]
        if len(pts) < 2:
            continue
        points = [(pt["x"] * SCALE, pt["y"] * SCALE, 0.02) for pt in pts]
        curve_data = bpy.data.curves.new(f"Road_{feature['id']}", "CURVE")
        curve_data.dimensions = "3D"
        curve_data.bevel_depth = 0.5
        spline = curve_data.splines.new("POLY")
        spline.points.add(len(points) - 1)
        for i, pt in enumerate(points):
            spline.points[i].co = (pt[0], pt[1], pt[2], 1)
        curve_obj = bpy.data.objects.new(f"Road_{feature['id']}", curve_data)
        bpy.context.collection.objects.link(curve_obj)
        mat = bpy.data.materials.new(name=f"Road_Mat_{feature['id']}")
        mat.use_nodes = True
        principled = mat.node_tree.nodes.get("Principled BSDF")
        principled.inputs["Base Color"].default_value = (0.3, 0.3, 0.3, 1.0)
        curve_obj.data.materials.append(mat)


def parser_features(result):
    features_by_type = {"building": [], "forest": [], "water": [], "road": []}
    for feature in result.features:
        ftype = feature.get("type", "unknown")
        if ftype in features_by_type:
            features_by_type[ftype].append(feature)

    if features_by_type["building"]:
        draw_buildings(features_by_type["building"])


# if features_by_type["forest"]:
#    draw_forests(features_by_type["forest"])
# if features_by_type["water"]:
#   draw_water(features_by_type["water"])
# if features_by_type["road"]:
#   draw_roads(features_by_type["road"])


if __name__ == "__main__":
    main()
