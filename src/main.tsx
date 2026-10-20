import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './workbench/App';
import './workbench/theme.css';
import './workbench/palette.css';
import './workbench/shell.css';
import { applyTheme,readTheme } from './workbench/appearance';

applyTheme(readTheme());

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
