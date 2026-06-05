import json
from dataclasses import dataclass
from pathlib import Path

from PIL.Image import Image

from gpx_con import Map, Route


@dataclass
class PipelineResult:
    map_image: Image
    terrain: list[dict]
    route_xyz: list[dict]
    grid_size: int
    raw_points: list[tuple[float, float]]
    route_elevations: list[float]
    bounds: tuple[int, int, int, int]


def from_file(json_path: str | Path, image_path: str | Path) -> PipelineResult:
    with open(json_path) as f:
        data = json.load(f)

    map_image = Image.open(image_path)

    return PipelineResult(
        map_image=map_image,
        terrain=data["terrain"],
        route_xyz=data["route"],
        grid_size=data["grid_size"],
        raw_points=[],
        route_elevations=[],
        bounds=(0, 0, 0, 0),
    )


def run(gpx_path: str | Path, zoom: int = 16) -> PipelineResult:
    route = Route(str(gpx_path))
    raw_points, route_elevations = route.parse_gpx("output.json")

    map_instance = Map()
    x_min, x_max, y_min, y_max = map_instance.bonding_area(raw_points)
    grid_coords = map_instance.gettingGrind(x_min, x_max, y_min, y_max, zoom)
    terrain_elevations = map_instance.gettingElevation(grid_coords)

    output_json = "output.json"
    route.transformationCord(
        raw_points, output_json, grid_coords, terrain_elevations, route_elevations
    )

    with open(output_json) as f:
        output_data = json.load(f)

    map_image = map_instance.getTiles(x_min, x_max, y_min, y_max, zoom)

    return PipelineResult(
        map_image=map_image,
        terrain=output_data["terrain"],
        route_xyz=output_data["route"],
        grid_size=output_data["grid_size"],
        raw_points=raw_points,
        route_elevations=route_elevations,
        bounds=(x_min, x_max, y_min, y_max),
    )
