import { lazy, Suspense, useEffect, useState } from "react";
import MapView from "./MapView";

const Scene3D = lazy(() => import("./Scene3D"));

const routeOf = () => (window.location.hash.startsWith("#/3d") ? "3d" : "map");

export default function App() {
  const [route, setRoute] = useState(routeOf);

  useEffect(() => {
    const on = () => setRoute(routeOf());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);

  return (
    <>
      {route === "3d" ? (
        <Suspense fallback={<div className="loading">Carregando cena 3D…</div>}>
          <Scene3D />
        </Suspense>
      ) : (
        <MapView />
      )}
      <a className="toggle" href={route === "3d" ? "#/" : "#/3d"}>
        {route === "3d" ? "Ver mapa" : "Ver 3D"}
      </a>
    </>
  );
}
