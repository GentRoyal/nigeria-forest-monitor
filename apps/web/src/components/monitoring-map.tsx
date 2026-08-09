"use client";

import * as maplibregl from "maplibre-gl";
import type { StyleSpecification } from "maplibre-gl";
import { useEffect, useRef } from "react";

const localStyle: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [
    {
      id: "background",
      type: "background",
      paint: { "background-color": "#dfe8df" }
    }
  ]
};

export function MonitoringMap() {
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!container.current) return;

    const map = new maplibregl.Map({
      container: container.current,
      style: localStyle,
      center: [4.0, 9.0],
      zoom: 6
    });

    map.addControl(new maplibregl.NavigationControl(), "top-right");
    map.on("load", () => {
      map.addSource("pilot-corridor", {
        type: "geojson",
        data: {
          type: "Feature",
          properties: { name: "Old Oyo–Kwara–Kainji pilot corridor" },
          geometry: {
            type: "Polygon",
            coordinates: [[[2.8, 7.8], [5.2, 7.8], [5.2, 10.2], [2.8, 10.2], [2.8, 7.8]]]
          }
        }
      });
      map.addLayer({
        id: "pilot-fill",
        type: "fill",
        source: "pilot-corridor",
        paint: { "fill-color": "#217346", "fill-opacity": 0.2 }
      });
      map.addLayer({
        id: "pilot-outline",
        type: "line",
        source: "pilot-corridor",
        paint: { "line-color": "#14532d", "line-width": 2 }
      });
    });

    return () => map.remove();
  }, []);

  return <div ref={container} className="map" aria-label="Pilot monitoring map" />;
}
