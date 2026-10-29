import React from 'react';
import { createRoot } from 'react-dom/client';
import WebEntry from './portal/WebEntry';
import './workbench/theme.css';
import './workbench/palette.css';
import './workbench/shell.css';
import { applyTheme,readTheme } from './workbench/appearance';

applyTheme(readTheme());

createRoot(document.getElementById('root')!).render(<React.StrictMode><WebEntry/></React.StrictMode>);
