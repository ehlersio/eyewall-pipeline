-- Hard-tier trivia batch — hand-curated, sourced from public trivia sites
-- (randomtrivia.co, everythingtrivia.com, icebreakerideas.com; a fourth,
-- triviawithanswers.com, had a high error rate and was mostly excluded),
-- cross-checked against known NHL/PWHL history and corrected where the
-- source was wrong or stale. Two corrections worth noting:
--   - "most career goals" updated to Alex Ovechkin (passed Gretzky's 894
--     in April 2025) -- every source still said Gretzky.
--   - "most wins in a season" updated to the 2022-23 Boston Bruins (65) --
--     every source still said the 1995-96 Red Wings' 62, broken since.
-- Run this in the Supabase SQL editor -- this repo has no migration
-- tooling, same convention as docs/session92_trivia_tables.sql.
--
-- Dated sequentially starting 2026-08-13 (tomorrow) so today's existing
-- fallback content (the two 2026-08-05 seed rows) rolls over cleanly once
-- this batch is applied. `team` defaults to 'ALL', `source` defaults to
-- 'ai' at the schema level so it's set explicitly here to 'curated'.

insert into public.trivia_questions
  (question_date, tier, sport, question_text, options, correct_index, explanation, source)
values

-- ============================== NHL (25) ==============================
('2026-08-13', 'hard', 'nhl',
 'Which team has won the most Stanley Cup championships in NHL history?',
 '["Montreal Canadiens", "Toronto Maple Leafs", "Detroit Red Wings", "Boston Bruins"]'::jsonb, 0,
 'The Montreal Canadiens have won 24 Stanley Cups, the most of any NHL franchise.', 'curated'),

('2026-08-14', 'hard', 'nhl',
 'Which player broke the NHL''s color barrier, debuting for the Boston Bruins in 1958?',
 '["Willie O''Ree", "Grant Fuhr", "Jarome Iginla", "Mike Marson"]'::jsonb, 0,
 'Willie O''Ree debuted for the Boston Bruins on January 18, 1958, becoming the NHL''s first Black player.', 'curated'),

('2026-08-15', 'hard', 'nhl',
 'Which of these teams was NOT part of the NHL''s "Original Six" era (1942-1967)?',
 '["Chicago Blackhawks", "Philadelphia Flyers", "Detroit Red Wings", "New York Rangers"]'::jsonb, 1,
 'The Original Six were Boston, Chicago, Detroit, Montreal, the Rangers, and Toronto; the Flyers joined in the 1967 expansion.', 'curated'),

('2026-08-16', 'hard', 'nhl',
 'How many teams did the NHL have after its landmark 1967 expansion, which doubled the league in size?',
 '["12", "10", "14", "16"]'::jsonb, 0,
 'The 1967 expansion doubled the NHL from six to twelve teams, adding the Kings, Flyers, Penguins, Blues, North Stars, and Seals.', 'curated'),

('2026-08-17', 'hard', 'nhl',
 'Which of these teams did NOT join the NHL from the WHA in the 1979 merger?',
 '["Edmonton Oilers", "Hartford Whalers", "Quebec Nordiques", "Vancouver Canucks"]'::jsonb, 3,
 'Edmonton, Hartford, Quebec, and Winnipeg joined from the WHA in 1979; Vancouver had already been an NHL team since 1970.', 'curated'),

('2026-08-18', 'hard', 'nhl',
 'Which player was traded from the Edmonton Oilers to the Los Angeles Kings in 1988, in one of the most famous trades in sports history?',
 '["Wayne Gretzky", "Mark Messier", "Paul Coffey", "Jari Kurri"]'::jsonb, 0,
 'The Gretzky trade to LA in August 1988 reshaped the NHL''s footprint in the US.', 'curated'),

('2026-08-19', 'hard', 'nhl',
 'Who holds the NHL''s all-time career regular-season points record?',
 '["Wayne Gretzky", "Jaromir Jagr", "Gordie Howe", "Mark Messier"]'::jsonb, 0,
 'Wayne Gretzky''s 2,857 career points remain the NHL''s all-time record.', 'curated'),

('2026-08-20', 'hard', 'nhl',
 'Who now holds the NHL''s all-time career goals record, having passed Wayne Gretzky''s total in 2025?',
 '["Alex Ovechkin", "Jaromir Jagr", "Mike Modano", "Marcel Dionne"]'::jsonb, 0,
 'Alex Ovechkin passed Wayne Gretzky''s 894 career goals in April 2025 to become the NHL''s all-time leader.', 'curated'),

('2026-08-21', 'hard', 'nhl',
 'Which team set the modern NHL record for most wins in a single regular season, with 65?',
 '["Boston Bruins", "Detroit Red Wings", "Tampa Bay Lightning", "Colorado Avalanche"]'::jsonb, 0,
 'The 2022-23 Boston Bruins went 65-12-5, breaking the Detroit Red Wings'' 1995-96 mark of 62 wins.', 'curated'),

('2026-08-22', 'hard', 'nhl',
 'Who holds the NHL record for most goals in a single season?',
 '["Wayne Gretzky", "Mario Lemieux", "Alex Ovechkin", "Brett Hull"]'::jsonb, 0,
 'Wayne Gretzky scored 92 goals in the 1981-82 season, still the single-season record.', 'curated'),

('2026-08-23', 'hard', 'nhl',
 'Who holds the NHL record for most assists in a single season?',
 '["Wayne Gretzky", "Bobby Orr", "Mario Lemieux", "Adam Oates"]'::jsonb, 0,
 'Gretzky notched 163 assists in the 1985-86 season, an NHL record.', 'curated'),

('2026-08-24', 'hard', 'nhl',
 'Who was the first player to score 50 goals in his team''s first 50 games of a season?',
 '["Maurice Richard", "Mike Bossy", "Wayne Gretzky", "Brett Hull"]'::jsonb, 0,
 'Maurice "Rocket" Richard reached 50 goals in the Canadiens'' 50th game of the 1944-45 season.', 'curated'),

('2026-08-25', 'hard', 'nhl',
 'In how many games did Wayne Gretzky reach 50 goals during the 1981-82 season, still the fastest in NHL history?',
 '["39", "50", "45", "34"]'::jsonb, 0,
 'Gretzky hit 50 goals in just 39 games in 1981-82, still the fastest in NHL history.', 'curated'),

('2026-08-26', 'hard', 'nhl',
 'Which defenseman holds the NHL record for most points in a single season by a blueliner?',
 '["Bobby Orr", "Paul Coffey", "Ray Bourque", "Al MacInnis"]'::jsonb, 0,
 'Bobby Orr scored 139 points in 1970-71, still the single-season record for a defenseman.', 'curated'),

('2026-08-27', 'hard', 'nhl',
 'Which defenseman holds the NHL record for most goals in a single season?',
 '["Paul Coffey", "Bobby Orr", "Al MacInnis", "Ray Bourque"]'::jsonb, 0,
 'Paul Coffey scored 48 goals in 1985-86, still the single-season record for a defenseman.', 'curated'),

('2026-08-28', 'hard', 'nhl',
 'Which goaltender holds the NHL''s career wins record?',
 '["Martin Brodeur", "Patrick Roy", "Roberto Luongo", "Marc-Andre Fleury"]'::jsonb, 0,
 'Brodeur retired with 691 career wins, the most in NHL history.', 'curated'),

('2026-08-29', 'hard', 'nhl',
 'Which goaltender holds the NHL''s career shutouts record?',
 '["Martin Brodeur", "Terry Sawchuk", "Glenn Hall", "Dominik Hasek"]'::jsonb, 0,
 'Brodeur finished his career with 125 shutouts, the most in NHL history.', 'curated'),

('2026-08-30', 'hard', 'nhl',
 'Which goaltender is credited with popularizing the modern goalie mask, after debuting one in a November 1959 game?',
 '["Jacques Plante", "Terry Sawchuk", "Glenn Hall", "Ken Dryden"]'::jsonb, 0,
 'Jacques Plante wore a mask in a November 1959 game and never played without one again, setting the trend.', 'curated'),

('2026-08-31', 'hard', 'nhl',
 'Which goaltender is the only one to win the Hart Trophy as NHL MVP twice?',
 '["Dominik Hasek", "Jacques Plante", "Bernie Parent", "Martin Brodeur"]'::jsonb, 0,
 'Dominik Hasek won the Hart Trophy in both 1997 and 1998; no other goaltender has won it more than once.', 'curated'),

('2026-09-01', 'hard', 'nhl',
 'Which goaltender was the first credited with a goal, after the puck deflected in off an opponent in 1979?',
 '["Billy Smith", "Ron Hextall", "Martin Brodeur", "Chris Osgood"]'::jsonb, 0,
 'Billy Smith of the New York Islanders was credited with a goal on November 28, 1979, when the puck went in off an opponent after he was the last Islander to touch it.', 'curated'),

('2026-09-02', 'hard', 'nhl',
 'Which goaltender became the first to score a goal by directly shooting the puck the length of the ice into an empty net?',
 '["Ron Hextall", "Billy Smith", "Martin Brodeur", "Chris Osgood"]'::jsonb, 0,
 'Ron Hextall shot the puck into an empty net in December 1987, the first goalie to score by directly shooting it in.', 'curated'),

('2026-09-03', 'hard', 'nhl',
 'How many consecutive Stanley Cups did the Montreal Canadiens win from 1956 to 1960?',
 '["5", "4", "3", "6"]'::jsonb, 0,
 'Montreal''s 1956-1960 run of five straight Cups remains the longest in NHL history.', 'curated'),

('2026-09-04', 'hard', 'nhl',
 'Which player holds the NHL record for most career Stanley Cup championships won as a player?',
 '["Henri Richard", "Jean Beliveau", "Maurice Richard", "Yvan Cournoyer"]'::jsonb, 0,
 'Henri Richard won 11 Stanley Cups as a player with the Montreal Canadiens, the most of anyone in NHL history.', 'curated'),

('2026-09-05', 'hard', 'nhl',
 'Which expansion team stunned the league by reaching the Stanley Cup Final in its very first season, 2017-18?',
 '["Vegas Golden Knights", "Seattle Kraken", "Columbus Blue Jackets", "Nashville Predators"]'::jsonb, 0,
 'The Vegas Golden Knights reached the Cup Final in their inaugural 2017-18 season, an unprecedented feat for an expansion team.', 'curated'),

('2026-09-06', 'hard', 'nhl',
 'Who scored the fastest hat trick in NHL history, netting three goals in just 21 seconds?',
 '["Bill Mosienko", "Jean Beliveau", "Wayne Gretzky", "Mario Lemieux"]'::jsonb, 0,
 'Bill Mosienko scored three goals in 21 seconds for the Chicago Blackhawks on March 23, 1952, still the fastest hat trick ever.', 'curated'),

-- ============================ PWHL (12) ============================
('2026-08-13', 'hard', 'pwhl',
 'How many teams made up the PWHL''s inaugural 2023-24 season?',
 '["6", "8", "4", "12"]'::jsonb, 0,
 'The PWHL launched in 2023-24 with six teams: Boston, Minnesota, Montreal, New York, Ottawa, and Toronto.', 'curated'),

('2026-08-14', 'hard', 'pwhl',
 'Which trophy is awarded to the PWHL''s playoff champion?',
 '["Isobel Cup", "Clarkson Cup", "Kazmaier Cup", "Riveters Cup"]'::jsonb, 0,
 'The Isobel Cup, named for Lady Isobel Gathorne-Hardy (daughter of Lord Stanley), has been the PWHL''s championship trophy since 2024.', 'curated'),

('2026-08-15', 'hard', 'pwhl',
 'Which trophy was the championship trophy of the CWHL, a predecessor women''s pro league that folded in 2019?',
 '["Clarkson Cup", "Isobel Cup", "Riveters Cup", "Kazmaier Cup"]'::jsonb, 0,
 'The Clarkson Cup, named for former Governor General Adrienne Clarkson, was the CWHL''s championship trophy.', 'curated'),

('2026-08-16', 'hard', 'pwhl',
 'In what year did women''s ice hockey debut as an Olympic event?',
 '["1998", "1994", "2002", "1988"]'::jsonb, 0,
 'Women''s ice hockey debuted at the 1998 Nagano Olympics, where the United States won gold.', 'curated'),

('2026-08-17', 'hard', 'pwhl',
 'Which country won the first-ever Olympic gold medal in women''s ice hockey, in 1998?',
 '["United States", "Canada", "Sweden", "Finland"]'::jsonb, 0,
 'The United States beat Canada in the final to win the inaugural women''s Olympic hockey gold in 1998.', 'curated'),

('2026-08-18', 'hard', 'pwhl',
 'Which country won four consecutive Olympic golds in women''s hockey from 2002 to 2014?',
 '["Canada", "United States", "Sweden", "Russia"]'::jsonb, 0,
 'Canada''s women''s team won gold at the 2002, 2006, 2010, and 2014 Winter Olympics.', 'curated'),

('2026-08-19', 'hard', 'pwhl',
 'Who scored the shootout-winning goal as the United States beat Canada for the 2018 Olympic women''s hockey gold?',
 '["Jocelyne Lamoureux-Davidson", "Hilary Knight", "Kendall Coyne Schofield", "Brianna Decker"]'::jsonb, 0,
 'Jocelyne Lamoureux-Davidson''s shootout move and goal clinched gold for the US at the 2018 PyeongChang Olympics.', 'curated'),

('2026-08-20', 'hard', 'pwhl',
 'Which player became the first woman to appear in NHL game action, suiting up in goal for a 1992 Tampa Bay Lightning preseason game?',
 '["Manon Rheaume", "Shannon Szabados", "Kim St-Pierre", "Erin Whitten"]'::jsonb, 0,
 'Manon Rheaume played in a 1992 Tampa Bay Lightning preseason game, becoming the first woman to play in an NHL game of any kind.', 'curated'),

('2026-08-21', 'hard', 'pwhl',
 'Who were the first two women inducted into the Hockey Hall of Fame, in 2010?',
 '["Angela James and Cammi Granato", "Hayley Wickenheiser and Hilary Knight", "Manon Rheaume and Cammi Granato", "Angela James and Hayley Wickenheiser"]'::jsonb, 0,
 'Angela James and Cammi Granato became the first women inducted into the Hockey Hall of Fame in 2010.', 'curated'),

('2026-08-22', 'hard', 'pwhl',
 'Which award is given annually to the top player in NCAA women''s college hockey?',
 '["Patty Kazmaier Award", "Isobel Cup", "Frozen Four MVP", "Clarkson Award"]'::jsonb, 0,
 'The Patty Kazmaier Memorial Award has honored the top NCAA women''s hockey player annually since 1998.', 'curated'),

('2026-08-23', 'hard', 'pwhl',
 'Which Canadian forward is nicknamed "Captain Clutch" for scoring Canada''s Olympic-gold-winning goal in both 2010 and 2014?',
 '["Marie-Philip Poulin", "Hayley Wickenheiser", "Cassie Campbell-Pascall", "Natalie Spooner"]'::jsonb, 0,
 'Marie-Philip Poulin scored the gold-medal-winning goal for Canada in both 2010 and 2014, earning the nickname "Captain Clutch."', 'curated'),

('2026-08-24', 'hard', 'pwhl',
 'Which goaltender started in net for Canada in both the 2010 and 2014 Olympic women''s hockey gold-medal games?',
 '["Shannon Szabados", "Kim St-Pierre", "Charline Labonte", "Ann-Renee Desbiens"]'::jsonb, 0,
 'Shannon Szabados started in net for both of Canada''s 2010 and 2014 Olympic gold-medal wins.', 'curated')

on conflict (question_date, tier, sport, team) do nothing;
