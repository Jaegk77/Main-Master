(= (main-step (incoming $in) (next-id $id) (now $ts))
   (list
     (assistant-json $id "{\"goal\":\"Assist user request\",\"plan\":[\"Restate task\",\"Decompose steps\",\"Select next actions\",\"Execute tools\",\"Evaluate results\"],\"next_actions\":[],\"assumptions\":[],\"questions\":[],\"progress\":[],\"status\":\"idle\"}")))

(= (agent-tick (now $ts))
   (list
     (assistant-json $ts "{\"goal\":\"Advance tasks\",\"plan\":[\"Select task\",\"Execute step\",\"Evaluate\"],\"next_actions\":[],\"assumptions\":[],\"questions\":[],\"progress\":[],\"status\":\"working\"}")))
