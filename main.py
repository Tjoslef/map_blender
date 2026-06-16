import math
import os
import sys
from pathlib import Path

import bpy
from mathutils import Vector

BASE_DIR = Path(__file__).resolve().parent

abs_path = str(BASE_DIR)
# 2. Tell Blender's Python to look in this folder for other modules
if BASE_DIR not in sys.path:
    sys.path.append(abs_path)

from pipeline import run

GPX_PATH = BASE_DIR / "Morning_Run.gpx"
IMAGE_PATH = BASE_DIR / "map_tiles.png"
BLEND_PATH = BASE_DIR / "gpx_map.blend"
OUTPUTJSON = BASE_DIR / "output.json"
SCALE = 0.1  # meters → more manageable size


def setup_geometry_nodes_scatter(floor_obj, tree_obj):
    import math

    # Clean up any existing modifier with same name
    if "ForestScatter" in floor_obj.modifiers:
        floor_obj.modifiers.remove(floor_obj.modifiers["ForestScatter"])

    mod = floor_obj.modifiers.new("ForestScatter", "NODES")
    node_group = bpy.data.node_groups.new("ForestScatter", "GeometryNodeTree")
    mod.node_group = node_group

    nodes = node_group.nodes
    links = node_group.links

    # --- Interface ---
    node_group.interface.new_socket(
        "Geometry", in_out="INPUT", socket_type="NodeSocketGeometry"
    )
    node_group.interface.new_socket(
        "Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry"
    )

    group_in = nodes.new("NodeGroupInput")
    group_in.location = (-800, 0)
    group_out = nodes.new("NodeGroupOutput")
    group_out.location = (1200, 0)

    # --- Distribute Points on Faces ---
    distribute = nodes.new("GeometryNodeDistributePointsOnFaces")
    distribute.location = (-500, 0)
    distribute.inputs["Density"].default_value = 0.001
    links.new(group_in.outputs[0], distribute.inputs["Mesh"])

    # --- Object Info (your tree) ---
    obj_info = nodes.new("GeometryNodeObjectInfo")
    obj_info.location = (-500, -250)
    obj_info.inputs["Object"].default_value = tree_obj
    obj_info.transform_space = "ORIGINAL"

    # --- Instance on Points ---
    # distribute.outputs["Rotation"] already encodes the face normal — no align node needed
    instance = nodes.new("GeometryNodeInstanceOnPoints")
    instance.location = (-150, 0)
    links.new(distribute.outputs["Points"], instance.inputs["Points"])
    links.new(distribute.outputs["Rotation"], instance.inputs["Rotation"])
    links.new(obj_info.outputs["Geometry"], instance.inputs["Instance"])

    # --- Random Z rotation (so trees don't all face same direction) ---
    rand_z = nodes.new("FunctionNodeRandomValue")
    rand_z.location = (-150, -250)
    rand_z.data_type = "FLOAT_VECTOR"
    rand_z.inputs["Min"].default_value = (0.0, 0.0, 0.0)
    rand_z.inputs["Max"].default_value = (0.0, 0.0, math.tau)

    rotate_inst = nodes.new("GeometryNodeRotateInstances")
    rotate_inst.location = (150, 0)
    rotate_inst.inputs["Local Space"].default_value = True
    links.new(instance.outputs["Instances"], rotate_inst.inputs["Instances"])
    links.new(rand_z.outputs["Value"], rotate_inst.inputs["Rotation"])

    # --- Random scale variation ---
    rand_scale = nodes.new("FunctionNodeRandomValue")
    rand_scale.location = (150, -250)
    rand_scale.data_type = "FLOAT_VECTOR"
    rand_scale.inputs["Min"].default_value = (0.8, 0.8, 0.8)
    rand_scale.inputs["Max"].default_value = (1.3, 1.3, 1.6)

    scale_inst = nodes.new("GeometryNodeScaleInstances")
    scale_inst.location = (450, 0)
    links.new(rotate_inst.outputs["Instances"], scale_inst.inputs["Instances"])
    links.new(rand_scale.outputs["Value"], scale_inst.inputs["Scale"])

    # --- Join floor + instances ---
    join = nodes.new("GeometryNodeJoinGeometry")
    join.location = (800, 0)
    links.new(scale_inst.outputs["Instances"], join.inputs["Geometry"])
    links.new(group_in.outputs[0], join.inputs["Geometry"])
    links.new(join.outputs["Geometry"], group_out.inputs[0])


def create_forest_floor(coords, name="ForestFloor"):
    import bmesh

    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)

    bm = bmesh.new()
    verts = [bm.verts.new((x, y, z)) for x, y, z in coords[:-1]]

    try:
        bm.faces.new(verts)
    except Exception as e:
        print(f"[{name}] Face creation failed: {e}")
        bm.free()
        return obj

    bm.faces.ensure_lookup_table()
    bm.verts.ensure_lookup_table()

    # Recalc first to get consistent state
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

    # Then force every face normal to point up — reliable regardless of winding
    for face in bm.faces:
        if face.normal.dot(Vector((0, 0, 1))) < 0:
            bmesh.ops.reverse_faces(
                bm, faces=[face]
            )  # <-- correct API, not face.normal_flip()

    bm.to_mesh(mesh)
    bm.free()

    # Recalc normals on the final mesh too

    mat = bpy.data.materials.new(name + "_mat")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (0.08, 0.25, 0.05, 1.0)
    bsdf.inputs["Alpha"].default_value = 0.5
    mat.blend_method = "BLEND"
    obj.data.materials.append(mat)

    return obj


def light():
    for obj in bpy.data.objects:
        if obj.type == "LIGHT":
            bpy.data.objects.remove(obj)

    sun = bpy.data.lights.new("Sun", "SUN")
    sun.energy = 2.0
    sun.angle = math.radians(2)

    sun_obj = bpy.data.objects.new("Sun", sun)
    bpy.context.collection.objects.link(sun_obj)
    sun_obj.rotation_euler = (math.radians(45), 0, math.radians(30))

    # Configure World (Ambient Light)
    world = bpy.context.scene.world
    world.use_nodes = True
    bg = world.node_tree.nodes.new("ShaderNodeBackground")

    # Made the base color a much darker midnight blue
    bg.inputs["Color"].default_value = (0.05, 0.05, 0.08, 1)

    # CRITICAL: Lowered strength from 0.2 to 0.02. This is what makes shadows actually dark.
    bg.inputs["Strength"].default_value = 0.02

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


def get_terrain_z(x, y, terrain_pts):
    """Find z of the nearest terrain point to (x, y)"""
    nearest = min(terrain_pts, key=lambda p: (p["x"] - x) ** 2 + (p["y"] - y) ** 2)
    return nearest["z"]


def main():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)

    result = run(GPX_PATH, 16)
    result.map_image.save(IMAGE_PATH)
    res = result.grid_size
    vertices = [
        (pt["x"] * SCALE, pt["y"] * SCALE, pt["z"] * SCALE) for pt in result.terrain
    ]
    xs = [pt["x"] for pt in result.terrain]
    ys = [pt["y"] for pt in result.terrain]
    print(f"Terrain X: {min(xs):.1f} to {max(xs):.1f}")
    print(f"Terrain Y: {min(ys):.1f} to {max(ys):.1f}")
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

    hue_sat = nodes.new(type="ShaderNodeHueSaturation")
    hue_sat.inputs["Saturation"].default_value = 2.2  # 1.0 = neutral, 2.0+ = vivid
    hue_sat.inputs["Value"].default_value = 1.1  # slight brightness boost
    hue_sat.inputs["Hue"].default_value = 0.5  # 0.5 = no hue shift

    # 2. Color Ramp — increase contrast, crush blacks, lift highlights
    ramp = nodes.new(type="ShaderNodeValToRGB")
    ramp.color_ramp.interpolation = "EASE"
    ramp.color_ramp.elements[0].position = 0.15  # crush dark end
    ramp.color_ramp.elements[1].position = 0.90  # lift bright end
    # make it a color ramp not greyscale — keep RGB by feeding Color not Value
    ramp.color_ramp.color_mode = "RGB"

    # 3. Gamma — subtle, just 1.1 now since ramp does the heavy lifting
    gamma_node = nodes.new(type="ShaderNodeGamma")
    gamma_node.inputs["Gamma"].default_value = 1.1

    # 2. Přidáme Bump uzel pro 3D efekt terénu
    bump_node = nodes.new(type="ShaderNodeBump")
    bump_node.inputs["Strength"].default_value = 0.5  # Síla reliéfu

    # --- Wire it up ---
    # Texture -> HueSat -> Ramp -> Gamma -> Base Color
    links.new(texture_node.outputs["Color"], hue_sat.inputs["Color"])
    links.new(hue_sat.outputs["Color"], ramp.inputs["Fac"])  # ramp on luminance
    links.new(hue_sat.outputs["Color"], gamma_node.inputs["Color"])  # color stays vivid
    links.new(gamma_node.outputs["Color"], principled.inputs["Base Color"])

    # Bump from original texture
    links.new(texture_node.outputs["Color"], bump_node.inputs["Height"])
    links.new(bump_node.outputs["Normal"], principled.inputs["Normal"])

    # Less metallic/specular so colors read more matte and saturated
    principled.inputs["Roughness"].default_value = 0.85
    principled.inputs["Specular IOR Level"].default_value = 0.1

    # Přiřadíme hotový materiál našemu terénu
    if len(terrain_obj.data.materials) == 0:
        terrain_obj.data.materials.append(mat)
    else:
        terrain_obj.data.materials[0] = mat
    parser_features(result)

    bpy.ops.wm.save_as_mainfile(filepath=str(BLEND_PATH))
    print(f"Saved Blender scene to {BLEND_PATH}")
    print("3D Terén s texturou byl úspěšně vygenerován!")
    print(f"Min and Max coordination {result.bounds}")


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


def draw_forests(forests, terrain_pts):
    base_dir = Path(BASE_DIR)
    tree_blend = base_dir / "tree.blend"

    if not tree_blend.exists():
        print(f"ERROR: File not found at {tree_blend}")
        return

    inner_dir = "Collection"
    collection_name = "tree"  # Change this if your collection has a different name

    filepath = str(tree_blend / inner_dir / collection_name).replace("\\", "/")
    directory = str(tree_blend / inner_dir).replace("\\", "/") + "/"

    print(f"Attempting to append from: {directory} with filename: {collection_name}")

    existing_collections = set(bpy.data.collections)

    try:
        bpy.ops.wm.append(
            filepath=filepath, directory=directory, filename=collection_name, link=False
        )
    except Exception as e:
        print(f"CRITICAL ERROR during append execution: {e}")
        return

    new_collections = set(bpy.data.collections) - existing_collections
    if new_collections:
        imported_collection = new_collections.pop()
        print(
            f"Successfully appended new collection data: '{imported_collection.name}'"
        )
    else:
        imported_collection = bpy.data.collections.get(collection_name)
        print(
            f"No new collection added. Using existing data block: '{collection_name}'"
        )

    if not imported_collection:
        print("ERROR: Could not find or access the target collection data block.")
        return

    if imported_collection.name not in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.link(imported_collection)

    mesh_objects = [obj for obj in imported_collection.objects if obj.type == "MESH"]
    print(f"Found {len(mesh_objects)} mesh objects inside the collection.")

    if not mesh_objects:
        print("ERROR: No mesh objects found to join.")
        return

    bpy.ops.object.select_all(action="DESELECT")
    final_objects_to_join = []
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for obj in mesh_objects:
        has_modifiers = any(mod.show_viewport for mod in obj.modifiers)
        if has_modifiers:
            print(f"Baking modifiers for: {obj.name} (Trunk/Branches)")
            obj_eval = obj.evaluated_get(depsgraph)
            baked_mesh = bpy.data.meshes.new_from_object(
                obj_eval, preserve_all_data_layers=True, depsgraph=depsgraph
            )
            new_obj = bpy.data.objects.new(
                name=f"Baked_{obj.name}", object_data=baked_mesh
            )
            imported_collection.objects.link(new_obj)
            new_obj.matrix_world = obj.matrix_world
            final_objects_to_join.append(new_obj)
            bpy.data.objects.remove(obj, do_unlink=True)
        else:
            print(f"Keeping original mesh: {obj.name} (Leaves - no modifiers needed)")
            final_objects_to_join.append(obj)
    bpy.context.view_layer.update()
    for obj in final_objects_to_join:
        obj.select_set(True)

    bpy.context.view_layer.objects.active = final_objects_to_join[0]

    print("Joining baked components and original leaves into a unified tree asset...")
    bpy.ops.object.join()

    tree_asset = bpy.context.active_object
    tree_asset.name = "ImportedTree"
    tree_asset.hide_viewport = True
    print(f"Tree asset '{tree_asset.name}' is verified and ready.")

    # 3. Process Forests and Build Floors
    for feature in forests:
        pts = feature["geometry"]
        print(f"Forest {feature['id']}: {len(pts)} pts, first={pts[0]}, last={pts[-1]}")
        if len(pts) < 3:
            print(
                f"Skipping Forest {feature['id']}: Not enough points to make a polygon floor."
            )
            continue

        coords = [
            (
                pt["x"] * SCALE,
                pt["y"] * SCALE,
                get_terrain_z(pt["x"], pt["y"], terrain_pts) * SCALE
                + 0.05,  # Assumes get_terrain_z is defined
            )
            for pt in pts
        ]

        # Build the geometry floor mesh (Assumes create_forest_floor is defined)
        floor = create_forest_floor(coords, name=f"Forest_{feature['id']}")

        # Inject the floor object and the tree asset into your scattering setup
        if floor:
            setup_geometry_nodes_scatter(floor, tree_asset)

    # 4. Global Coordinate Bounds Diagnostic
    forest_x = [p["x"] for feature in forests for p in feature["geometry"]]
    forest_y = [p["y"] for feature in forests for p in feature["geometry"]]

    if forest_x and forest_y:
        print(
            f"Forest bounds: X({min(forest_x):.2f} to {max(forest_x):.2f}), Y({min(forest_y):.2f} to {max(forest_y):.2f})"
        )


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

    # if features_by_type["building"]:
    #   draw_buildings(features_by_type["building"])
    if features_by_type["forest"]:
        draw_forests(features_by_type["forest"], result.terrain)


# if features_by_type["water"]:
#   draw_water(features_by_type["water"])
# if features_by_type["road"]:
#   draw_roads(features_by_type["road"])


if __name__ == "__main__":
    main()
