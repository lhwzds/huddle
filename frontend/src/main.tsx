import ReactDOM from 'react-dom/client';
import { BrowserRouter, Route, Routes } from 'react-router-dom';
import App from './App';
import { routerBasename } from './base';
import './styles.css';
ReactDOM.createRoot(document.getElementById('root')!).render(
  <BrowserRouter basename={routerBasename()}>
    <Routes>
      <Route path="/" element={<App />} />
      <Route path="/c/:id" element={<App />} />
    </Routes>
  </BrowserRouter>,
);
