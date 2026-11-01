import React from 'react';
import { createRoot } from 'react-dom/client';
import WebEntry from './portal/WebEntry';
import {PageErrorBoundary,SystemFeedback} from './workbench/SystemFeedback';
import './workbench/theme.css';
import './workbench/palette.css';
import './workbench/shell.css';
import './portal/modern.css';
import { applyTheme,readTheme } from './workbench/appearance';

applyTheme(readTheme());

createRoot(document.getElementById('root')!).render(<React.StrictMode><SystemFeedback/><PageErrorBoundary><WebEntry/></PageErrorBoundary></React.StrictMode>);
