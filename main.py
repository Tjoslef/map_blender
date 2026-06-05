import bpy

from pipeline import run as run_pipeline


def main():
    result = run_pipeline("Morning_Run.gpx")

    result.map_image.save("/tmp/map_tiles.png")

    bpy_image = bpy.data.images.load("/tmp/map_tiles.png")

    bpy.ops.mesh.primitive_grid_add(
        subdivisions_x=result.grid_size - 1,
        subdivisions_y=result.grid_size - 1,
        size=10,
    )
    terrain_mesh = bpy.context.active_object

    material = bpy.data.materials.new(name="MapTexture")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    texture_node = nodes.new(type="ShaderNodeTexImage")
    texture_node.image = bpy_image
    bsdf = nodes.get("Principled BSDF")
    material.node_tree.links.new(
        texture_node.outputs["Color"], bsdf.inputs["Base Color"]
    )
    terrain_mesh.data.materials.append(material)

    for vert, point in zip(terrain_mesh.data.vertices, result.terrain):
        vert.co.x = point["x"]
        vert.co.y = point["y"]
        vert.co.z = point["z"]

    print(
        f"Done. {len(result.terrain)} terrain points, {len(result.route_xyz)} route points"
    )


if __name__ == "__main__":
    main()
