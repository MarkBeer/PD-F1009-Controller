import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { CatalogProvider } from './CatalogContext'
import LoadView from './LoadView'
import NowPlaying from './NowPlaying'
import PlayView from './PlayView'
import './App.css'

export default function App() {
  return (
    <CatalogProvider>
      <div className="app-shell">
        <nav className="top-nav">
          <div className="brand">PD-F1009</div>
          <div className="nav-links">
            <NavLink to="/play">Play</NavLink>
            <NavLink to="/load">Set Up</NavLink>
          </div>
        </nav>
        <main className="main">
          <Routes>
            <Route path="/" element={<Navigate to="/play" replace />} />
            <Route path="/load" element={<LoadView />} />
            <Route path="/play" element={<PlayView />} />
          </Routes>
        </main>
        <NowPlaying />
      </div>
    </CatalogProvider>
  )
}
