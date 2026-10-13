import React from 'react';
import { createRoot } from 'react-dom/client';
import { ReactFlowProvider } from '@xyflow/react';
import flowCss from '@xyflow/react/dist/style.css';
import appCss from './styles.css';
import App from './App.jsx';

// One self-contained bundle: CSS is imported as text and injected, so the page works offline from one file.
const style = document.createElement('style');
style.textContent = flowCss + '\n' + appCss;
document.head.appendChild(style);

createRoot(document.getElementById('root')).render(
  <ReactFlowProvider><App /></ReactFlowProvider>,
);
