import {createState} from './ledger-state.js';
import {createController} from './ledger-controller.js';
import {message} from './ledger-view.js';

const controller=createController(createState());
controller.bind();
controller.refresh(true).catch(error=>message(error.message,true));
