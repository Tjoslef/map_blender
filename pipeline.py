import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypedDict

from PIL import Image

from gpx_con import CoordinateSystem, Map, OSMFeatures, Route


class XYPoint(TypedDict):
    x: float
    y: float


# 2. Define the structured OpenStreetMap feature format
class OSMFeatureItem(TypedDict):
    id: int
    type: Literal["building", "forest", "road", "water", "unknown"]
    tags: dict[str, Any]
    geometry: list[XYPoint]


@dataclass
class PipelineResult:
    map_image: Image.Image
    terrain: list[dict]
    route_xyz: list[dict]
    grid_size: int
    raw_points: list[tuple[float, float]]
    route_elevations: list[float]
    bounds: tuple[int, int, int, int]
    features: list[OSMFeatureItem]

    def __getitem__(self, key: str):
        key_map = {
            "terrain": self.terrain,
            "route": self.route_xyz,  # maps 'route' key to route_xyz attribute
            "route_xyz": self.route_xyz,
            "grid_size": self.grid_size,
            "features": self.features,
            "bounds": self.bounds,
            "map_image": self.map_image,
            "raw_points": self.raw_points,
            "route_elevations": self.route_elevations,
        }

        if key in key_map:
            return key_map[key]

        raise KeyError(f"'{key}' is not a valid attribute of PipelineResult")


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
        features=data["features"],
    )


def run(gpx_path: str | Path, zoom: int = 16) -> PipelineResult:
    route = Route(str(gpx_path))
    raw_points, route_elevations = route.parse_gpx("output.json")

    map_instance = Map()
    x_min, x_max, y_min, y_max = map_instance.bonding_area(raw_points)
    grid_coords = map_instance.gettingGrind(x_min, x_max, y_min, y_max, zoom)
    terrain_elevations = map_instance.gettingElevation(grid_coords)

    output_json = "output.json"
    osm_raw_data = OSMFeatures.fetch_features(x_min, x_max, y_min, y_max, zoom)
    cs = CoordinateSystem.create(
        grid_coords[0][0], grid_coords[0][1], route_elevations[0]
    )
    route.transformationCord(
        cs,
        raw_points,
        output_json,
        grid_coords,
        terrain_elevations,
        route_elevations,
        osm_features=osm_raw_data,
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
        features=output_data["features"],
    )
